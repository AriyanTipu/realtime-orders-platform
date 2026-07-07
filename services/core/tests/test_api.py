import pytest
from django.test import override_settings
from rest_framework.test import APIClient

import pickpath
from inventory.models import Stock
from orders.models import OrderStatus
from orders.services import OrderLine, place_order
from tests.factories import make_stock, make_user, make_variant, make_warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def catalog():
    warehouse = make_warehouse(code="LDN")
    mug = make_variant(sku="MUG-1", price_pence=799)
    bag = make_variant(sku="BAG-1", price_pence=4500)
    make_stock(mug, warehouse, quantity=10, bin_x=2, bin_y=3)
    make_stock(bag, warehouse, quantity=5, bin_x=8, bin_y=1)
    return warehouse, mug, bag


def test_place_order_via_api(client, catalog):
    warehouse, mug, bag = catalog
    response = client.post(
        "/api/orders/",
        {
            "warehouse": "LDN",
            "items": [
                {"sku": "MUG-1", "quantity": 2},
                {"sku": "MUG-1", "quantity": 1},  # duplicate lines are merged
                {"sku": "BAG-1", "quantity": 1},
            ],
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["total_pence"] == 3 * 799 + 4500
    assert {item["sku"]: item["quantity"] for item in body["items"]} == {"MUG-1": 3, "BAG-1": 1}
    assert Stock.objects.get(variant=mug).quantity == 7


def test_oversell_returns_409_with_shortages(client, catalog):
    response = client.post(
        "/api/orders/",
        {"warehouse": "LDN", "items": [{"sku": "BAG-1", "quantity": 50}]},
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["shortages"] == [{"sku": "BAG-1", "requested": 50, "available": 5}]


def test_unknown_sku_returns_400(client, catalog):
    response = client.post(
        "/api/orders/",
        {"warehouse": "LDN", "items": [{"sku": "NOPE-1", "quantity": 1}]},
        format="json",
    )
    assert response.status_code == 400


@override_settings(DEMO_MODE=False)
def test_anonymous_orders_rejected_outside_demo_mode(client, catalog):
    response = client.post(
        "/api/orders/",
        {"warehouse": "LDN", "items": [{"sku": "MUG-1", "quantity": 1}]},
        format="json",
    )
    # DRF maps NotAuthenticated to 403 when no authenticator issues a
    # WWW-Authenticate challenge; either way the request must be rejected.
    assert response.status_code in {401, 403}
    assert Stock.objects.get(variant__sku="MUG-1").quantity == 10


def test_order_detail_includes_status_events(client, catalog):
    warehouse, mug, _ = catalog
    order = place_order(
        user=make_user(),
        warehouse=warehouse,
        lines=[OrderLine(variant_id=mug.id, quantity=1)],
    )
    response = client.get(f"/api/orders/{order.public_id}/")
    assert response.status_code == 200
    body = response.json()
    assert body["public_id"] == str(order.public_id)
    assert body["status_events"][0]["to_status"] == "PENDING"


def test_advance_endpoint_enforces_lifecycle(client, catalog):
    warehouse, mug, _ = catalog
    order = place_order(
        user=make_user(),
        warehouse=warehouse,
        lines=[OrderLine(variant_id=mug.id, quantity=1)],
    )
    ok = client.post(
        f"/api/orders/{order.public_id}/advance/", {"to_status": "CONFIRMED"}, format="json"
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "CONFIRMED"

    bad = client.post(
        f"/api/orders/{order.public_id}/advance/", {"to_status": "DELIVERED"}, format="json"
    )
    assert bad.status_code == 400

    nonsense = client.post(
        f"/api/orders/{order.public_id}/advance/", {"to_status": "TELEPORTED"}, format="json"
    )
    assert nonsense.status_code == 400


def test_pick_path_endpoint(client, catalog):
    warehouse, mug, bag = catalog
    order = place_order(
        user=make_user(),
        warehouse=warehouse,
        lines=[OrderLine(variant_id=mug.id, quantity=2), OrderLine(variant_id=bag.id, quantity=1)],
    )
    response = client.get(f"/api/orders/{order.public_id}/pick-path/")
    assert response.status_code == 200
    body = response.json()
    assert body["engine"] in {"python", "native"}
    assert body["total_distance"] > 0
    assert len(body["stops"]) == 2  # two distinct bins
    assert body["warehouse"]["grid_width"] == 40
    visited_bins = {(stop["x"], stop["y"]) for stop in body["stops"]}
    assert visited_bins == {(2, 3), (8, 1)}


def test_pick_path_native_engine_rejected_when_unavailable(client, catalog):
    if pickpath.native_available():
        pytest.skip("native engine installed; rejection path not reachable")
    warehouse, mug, _ = catalog
    order = place_order(
        user=make_user(),
        warehouse=warehouse,
        lines=[OrderLine(variant_id=mug.id, quantity=1)],
    )
    response = client.get(f"/api/orders/{order.public_id}/pick-path/?engine=native")
    assert response.status_code == 400


def test_products_and_stock_endpoints(client, catalog):
    products = client.get("/api/products/")
    assert products.status_code == 200
    assert products.json()["count"] == 2

    stock = client.get("/api/stock/", {"warehouse": "LDN"})
    assert stock.status_code == 200
    rows = stock.json()["results"]
    assert {row["sku"] for row in rows} == {"MUG-1", "BAG-1"}

    empty = client.get("/api/stock/", {"warehouse": "NOPE"})
    assert empty.json()["count"] == 0


@override_settings(DEMO_MODE=False)
def test_demo_endpoint_hidden_outside_demo_mode(client, catalog):
    assert client.post("/api/demo/orders/").status_code == 404


def test_demo_endpoint_places_an_order(client, catalog):
    response = client.post("/api/demo/orders/")
    assert response.status_code in {200, 201}
    assert response.json()["placed"] is not None


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["database"] is True
