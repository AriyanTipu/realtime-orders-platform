# Query optimization: top sellers per warehouse, last 24 hours

The dashboard's analytics panel is served by a deliberately non-trivial
query — [analytics/queries/top_sellers.sql](../services/core/analytics/queries/top_sellers.sql):

- a three-table join (order items → orders → variants) plus two dimension
  joins for display names;
- a grouped aggregation (`SUM`, `COUNT(DISTINCT ...)`) per
  (warehouse, product);
- a window function
  `RANK() OVER (PARTITION BY warehouse_id ORDER BY units_sold DESC, ...)`
  to take the top N *per warehouse* — a per-group top-N, which a plain
  `LIMIT` cannot express;
- a selective predicate: `created_at >= now() - 24h AND status <> 'CANCELLED'`.

Everything below is **measured**, not asserted — via
`python manage.py explain_report`, which runs `VACUUM (ANALYZE)` first,
executes each variant twice and reports the warm run, and measures the
"without index" case by dropping the index inside a transaction that is
rolled back (PostgreSQL DDL is transactional, so both variants see identical
live data). Hardware: the project's dev machine; PostgreSQL 17.6 defaults.

## The index

```python
models.Index(
    fields=["created_at"],                      # the selective range key
    name="order_active_created_idx",
    condition=~models.Q(status="CANCELLED"),    # partial: cancelled rows can never match
    include=["warehouse", "id"],                # covering: GROUP BY key + JOIN key
)
```

## Act 1 — the first design was wrong, and EXPLAIN caught it

The first version carried `INCLUDE (warehouse_id)` but **not `id`**. Measured
result on 60k orders: the planner ignored the index completely — both
variants seq-scanned `orders_order` (~86 ms warm; the "with index" run even
looked *slower* purely from cold planner caches).

Two reasons, both instructive:

1. **The query joins on `o.id`** (`oi.order_id = o.id`). PostgreSQL secondary
   indexes do not implicitly carry the primary key (that's an InnoDB
   behaviour), so an index-only scan was impossible — every hit would need a
   heap visit. With ~12% of a *randomly ordered* table matching, essentially
   every heap page contains a match, so a heap-fetching index scan reads
   more pages than a straight seq scan. The planner was right to refuse.
2. **A freshly bulk-loaded table has no visibility map** until VACUUM runs,
   which alone rules out index-only scans regardless of index shape.

Lesson: *covering* means covering **the query** — join keys included — and
index-only scans are as much about table maintenance state as index shape.

## Act 2 — covering index, moderate selectivity (2.49%)

Dataset: 120,000 orders / 299,234 items over one recency-skewed year;
2,993 non-cancelled orders in the window.

```text
->  Index Only Scan using order_active_created_idx on orders_order o
      (actual time=0.205..1.849 rows=2993 loops=3)
      Heap Fetches: 0
```

The orders-side access drops from a 23.6 ms parallel seq scan to a 1.8 ms
index-only scan (**13× on the node the index targets**). End-to-end,
however: 140.1 ms → 131.0 ms. The plan's cost centre is now the *other*
side of the join — hashing 299k order items — and at 3k outer rows the
planner correctly judges per-row FK-index probing more expensive than one
hash build. An index is not magic; it removes the cost it targets and the
bottleneck moves.

## Act 3 — the crossover (0.09% selectivity)

Dataset: 240,000 orders / 599,587 items spread uniformly over three years;
214 non-cancelled orders in the window — realistic for "last 24 hours"
against years of history.

```text
Nested Loop  (actual time=0.049..2.010 rows=539 loops=1)
  ->  Index Only Scan using order_active_created_idx on orders_order o
        (actual time=0.026..0.200 rows=214 loops=1)
        Heap Fetches: 0
  ->  Index Scan using orders_orderitem_order_id_… on orders_orderitem oi
        (actual time=0.006..0.007 rows=3 loops=214)
Execution Time: 4.080 ms
```

Without the index, the same query parallel-seq-scans 240k rows:

```text
->  Parallel Seq Scan on orders_order o (actual time=0.186..23.380 …)
Execution Time: 104.976 ms
```

| Variant | Execution time | Speedup |
|---|---:|---:|
| With `order_active_created_idx` | **4.08 ms** | **25.7×** |
| Without (seq scan) | 104.98 ms | — |

At this selectivity the planner flips the whole join strategy: the
index-only scan feeds a nested loop that probes `orders_orderitem` through
its FK index (214 × ~3 probes). **Both sides of the plan now scale with the
window, not with total history** — the seq-scan variant keeps getting slower
as the business accumulates orders; the indexed variant does not.

## What deliberately has no index

- `orders_orderitem.order_id`, `variant_id` etc. — Django's automatic FK
  indexes already exist (Act 3's nested loop uses one).
- A `(status, created_at)` composite — `status <> 'CANCELLED'` is a poor
  B-tree key; the partial-index predicate encodes it for free and keeps the
  index smaller and hotter.
- Anything for the aggregation itself — grouping and ranking are CPU over
  matched rows; only selectivity reduction helps, and that's done.

## Reproduce

```bash
docker compose up -d db
docker compose run --rm core python manage.py seed_data --orders 240000 --days 1095 --recency-skew 1.0
docker compose run --rm core python manage.py explain_report
```
