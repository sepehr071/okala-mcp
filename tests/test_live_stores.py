import pytest
from conftest import POINT

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_ok_locate(client):
    data = await call(client, "ok_locate", {"address": "میدان ونک"})
    assert data["city_id"] == 129 and data["candidates"]
    top = data["candidates"][0]
    assert 35.6 < top["lat"] < 35.9 and 51.2 < top["lon"] < 51.6 and data["top_address"]["city"]


async def test_ok_stores(client):
    data = await call(client, "ok_stores", {**POINT, "open_only": False})
    assert data["city_id"] == 129 and data["count"] >= 10
    s = data["stores"][0]
    assert s["store_id"] and s["name"] and s["type"] and 0 <= s["distance_km"] < 20
    assert s["service_fee"] < 100_000  # Toman, not Rial


async def test_ok_store(client):
    data = await call(client, "ok_store", {"store_id": 11009, **POINT})
    assert data["name"] and 10_000 <= data["minimum_order"] <= 2_000_000
    assert data["location"]["lat"] and isinstance(data["delivery_days"], list)


async def test_ok_store_reviews(client):
    data = await call(client, "ok_store_reviews", {"store_id": 11009, "page_size": 3})
    assert data["total"] >= 1 and 1 <= data["reviews"][0]["rating"] <= 5
