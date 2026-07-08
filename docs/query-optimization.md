# Query optimization: top sellers per warehouse, last 24 hours

The dashboard's analytics panel is served by a deliberately non-trivial
query — [analytics/queries/top_sellers.sql](../services/core/analytics/queries/top_sellers.sql):

- a three-table join (order items → orders → variants) plus two dimension
  joins for display names;
- a grouped aggregation (`SUM`, `COUNT(DISTINCT ...)`) per
  (warehouse, product);
- a window function
  `RANK() OVER (PARTITION BY warehouse_id ORDER BY units_sold DESC, ...)`
  to take the top N *per warehouse* — a per-group top-N, which `LIMIT`
  cannot express;
- a selective time/status predicate:
  `created_at >= now() - 24h AND status <> 'CANCELLED'`.

## The index

Defined on the `Order` model and shipped in the initial migration:

```python
models.Index(
    fields=["created_at"],
    name="order_active_created_idx",
    condition=~models.Q(status="CANCELLED"),   # partial
    include=["warehouse"],                      # covering
)
```

Three deliberate choices:

1. **`created_at` as the key** — the 24-hour predicate is the selective one:
   it discards ~95% of a month of history before anything is joined or
   grouped. A B-tree range scan fits it exactly.
2. **Partial (`WHERE status <> 'CANCELLED'`)** — cancelled orders can never
   satisfy the query, so they don't belong in the index. The index stays
   smaller, hotter in cache, and cheaper to maintain than a two-column
   `(status, created_at)` alternative — and `status <> ...` is a poor B-tree
   key anyway.
3. **`INCLUDE (warehouse_id)`** — the query also needs each matching order's
   warehouse (its GROUP BY key). Carrying it in the index leaf makes the
   index *covering* for this query, enabling an index-only scan instead of a
   heap fetch per matching row.

What deliberately does **not** get an index: `orders_orderitem.order_id` and
the other FK joins are already covered by Django's automatic FK indexes, and
the aggregation itself is CPU over the matched rows — no index helps that.

## Measured plans

Generated with `python manage.py explain_report` (the command drops the
index inside a transaction and rolls back, so both variants are measured
against identical live data — PostgreSQL DDL is transactional).
Dataset: seeded history at default scale on the Compose Postgres 17
container. <!-- EXPLAIN-NUMBERS -->

_Measured output is inserted below by running the command; see README
"Query optimization" for the summary._

## How to reproduce

```bash
docker compose up -d db
docker compose run --rm core python manage.py seed_data
docker compose run --rm core python manage.py explain_report
```
