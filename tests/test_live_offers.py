import pytest
from conftest import POINT

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_ok_deals(client):
    deals = await call(client, "ok_deals", {**POINT, "store_id": 11009})
    assert deals["rows"] and deals["top_discounts"]["total"] >= 1
    row = deals["rows"][0]
    assert row["carousel_id"] and row["title"] and row["products"][0]["final_price"]


async def test_ok_offer(client):
    deals = await call(client, "ok_deals", {**POINT, "per_row": 1})
    row = next(r for r in deals["rows"] if r["store_count"] > 1)
    data = await call(client, "ok_offer", {"carousel_id": row["carousel_id"], **POINT, "sort": "cheapest"})
    prices = [p["final_price"] for p in data["products"]]
    assert data["total"] >= 1 and data["stores"] and prices == sorted(prices)
