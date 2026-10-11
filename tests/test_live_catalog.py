import pytest
from conftest import POINT

from okala_mcp import http

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_ok_categories(client):
    data = await call(client, "ok_categories", {**POINT, "depth": 2})
    slugs = {c["slug"] for c in data["categories"]}
    assert "dairy-products" in slugs and len(data["categories"]) >= 8


async def test_ok_search(client):
    # Text mode when this machine has a saved login (`okala-mcp login`), category mode otherwise.
    data = await call(client, "ok_search", {"query": "شیر", **POINT, "limit": 10})
    if data["mode"] == "category":
        assert any(c["slug"] == "milk" and c["used"] for c in data["categories"])
    else:
        assert data["stores"] >= 2 and data["suggestions"]
    assert data["products"] and "شیر" in data["products"][0]["name"].replace("ي", "ی")
    assert all(p["final_price"] < 10_000_000 for p in data["products"] if p["final_price"])  # Toman


async def test_ok_search_in_store(client):
    if not http.logged_in():
        pytest.skip("needs `okala-mcp login`")
    data = await call(client, "ok_search", {"query": "پنیر", "store_id": 11009, "limit": 5})
    assert data["mode"] == "text" and data["products"] and all(p["store_id"] == 11009 for p in data["products"])


async def test_ok_compare(client):
    data = await call(client, "ok_compare", {**POINT, "category_slug": "milk", "limit": 5})
    top = data["products"][0]
    assert top["store_count"] >= 2
    prices = [o["final_price"] for o in top["offers"]]
    assert prices == sorted(prices) and str(top["cheapest_store_id"]) in data["stores"]


async def test_ok_store_products(client):
    data = await call(
        client,
        "ok_store_products",
        {"store_id": 11009, "category_slug": "dairy-products", "sort": "cheapest", "limit": 10},
    )
    prices = [p["final_price"] for p in data["products"]]
    assert data["total"] >= 20 and prices == sorted(prices)


async def test_ok_product(client):
    data = await call(client, "ok_product", {"product_id": 269658, "store_id": 11009})
    assert data["name"] and data["brand_slug"] == "quinofit" and data["images"]
    assert not data["sold_here"] or 1_000 < data["final_price"] < 1_000_000


async def test_ok_brands(client):
    data = await call(client, "ok_brands", {"name": "kalleh"})
    assert data["brands"][0]["brand_id"] == 1
