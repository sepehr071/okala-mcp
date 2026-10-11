import pytest
from conftest import LAT, LON, POINT, fixture

pytestmark = pytest.mark.anyio

NEARBY = "/api/opex/v4/stores/nearby"
DETAILS = "/api/opex/v2/stores/details"
SCHEDULE = "/api/voyager/Order/GetActiveSchedule/1"
REVIEWS = "/api/review/v1/stores/11009/reviews"


def geo_routes(api):
    api["/api/voyager/C/Location/GetCitiesForReact"] = fixture("cities.json")
    api["/api/parsimap/forward"] = fixture("forward.json")
    api["/api/parsimap/reverse"] = fixture("reverse.json")


async def test_ok_locate(client, api):
    geo_routes(api)
    out = (await client.call_tool("ok_locate", {"address": "میدان ونک"})).structured_content
    assert (out["city"], out["city_id"]) == ("تهران", 129)
    assert out["candidates"][1] == {
        "title": "میدان ونک(کد 493)",
        "description": "تهران،گاندی،میدان ونک(کد 493)",
        "kind": "Poi",
        "lat": 35.754798,
        "lon": 51.410654,
    }
    assert out["top_address"] == {"address": "م. آرژانتین", "city": "تهران", "plate": "1"}
    assert api.params(1) == {"searchText": "میدان ونک", "district": "تهران", "onlyInDistrict": "false"}
    assert api.params(2)["location"] == "51.40994,35.757535"  # ParsiMap wants lon,lat


async def test_ok_locate_city_and_misses(client, api):
    geo_routes(api)
    out = (await client.call_tool("ok_locate", {"address": "x y", "city": "شيراز"})).structured_content  # Arabic ي
    assert out["city"] == "شیراز" and api.params(1)["district"] == "شیراز"

    result = await client.call_tool("ok_locate", {"address": "میدان ونک", "city": "لندن"})
    assert result.is_error and "Supported city names: " in result.content[0].text and "تهران" in result.content[0].text
    assert len([c for c in api.calls if "Cities" in c.url.path]) == 1  # cached

    api["/api/parsimap/forward"] = {"results": [], "status": "INVALID_PARAMETERS"}
    result = await client.call_tool("ok_locate", {"address": "zzzz"})
    assert result.is_error and "No place matches 'zzzz'" in result.content[0].text


async def test_ok_stores(client, api):
    api[NEARBY] = fixture("nearby.json")
    out = (await client.call_tool("ok_stores", POINT)).structured_content
    assert api.params() == {"latitude": str(LAT), "longitude": str(LON)}
    assert out["city_id"] == 129 and out["count"] == 4  # the closed store is left out
    assert out["types"]["SuperMarket"] == 23
    assert out["stores"][0] == {
        "store_id": 11009,
        "name": "حمید",
        "type": "SuperMarket",
        "rating": 4.1,
        "rating_count": 100,
        "distance_km": 1.1,
        "slot": "امروز - 07 تا 08",
        "slot_from": "2026-10-07T07:00:00",
        "slot_to": "2026-10-07T08:00:00",
        "is_closed": False,
        "service_fee": 10500,  # Rial 105000 -> Toman
        "packaging_fee": 3000,
        "delivery_fee": 0,
        "delivery_provider": "الوپیک",
        "labels": ["تضمین کیفیت"],
    }
    assert out["stores"][2]["type"] == "Bakery" and out["stores"][2]["service_fee"] == 0

    out = (
        await client.call_tool("ok_stores", {**POINT, "open_only": False, "store_type": "Bakery", "limit": 2})
    ).structured_content
    assert api.params()["storeTypes"] == "Bakery"
    assert out["count"] == 5 and len(out["stores"]) == 2


async def test_ok_stores_outside_coverage(client, api):
    api[NEARBY] = fixture("nearby_out.json")
    out = (await client.call_tool("ok_stores", {"lat": 30.0, "lon": 60.0})).structured_content
    assert out["count"] == 0 and out["city_id"] == 0 and "does not cover" in out["message"]

    result = await client.call_tool("ok_stores", {"lat": 51.5, "lon": 0.1})  # London: outside Iran bounds
    assert result.is_error and len(api.calls) == 1


async def test_ok_store(client, api):
    api[DETAILS] = fixture("details.json")
    api[SCHEDULE] = fixture("schedule.json")
    api[REVIEWS] = fixture("reviews.json")
    out = (await client.call_tool("ok_store", {"store_id": 11009, **POINT})).structured_content
    assert api.calls[0].url.params["StoreId"] == "11009" and api.calls[0].url.params["Latitude"] == str(LAT)
    assert out["serves_this_point"] is True and out["status"] == "فروشگاه فعال و در دسترس است"
    assert out["minimum_order"] == 200000  # 2,000,000 Rial
    assert (out["rating"], out["rating_count"], out["review_count"]) == (4.1, 100, 13)
    assert out["location"] == {"lat": 35.746546, "lon": 51.411692}
    day = out["delivery_days"][0]
    assert (day["date"], day["weekday"], day["available_slots"]) == ("2026-10-07", "چهارشنبه", 1)
    assert day["slots"][:2] == [
        {"time": "07:00-08:00", "available": True},
        {"time": "08:00-09:00", "available": False, "note": "ظرفیت تکمیل"},
    ]
    assert day["slots"][-1]["time"] == "21:00-22:00"


async def test_ok_store_unknown_and_out_of_coverage(client, api):
    api[DETAILS] = {
        "success": True,
        "data": {"storeName": None, "isExist": False, "storeStatusMessage": "فروشگاه یافت نشد"},
    }
    result = await client.call_tool("ok_store", {"store_id": 99999999, **POINT})
    assert result.is_error and "No Okala store with id 99999999" in result.content[0].text and len(api.calls) == 1

    details = fixture("details.json")
    details["data"] |= {"isServes": False, "storeStatusMessage": "فروشگاه در محدوده پوشش دهی قرار ندارد"}
    api[DETAILS] = details
    api[SCHEDULE] = fixture("schedule.json")
    api[REVIEWS] = fixture("reviews.json")
    out = (await client.call_tool("ok_store", {"store_id": 11009, **POINT})).structured_content
    assert out["serves_this_point"] is False and "پوشش" in out["status"]


async def test_ok_store_reviews(client, api):
    api[REVIEWS] = fixture("reviews.json")
    out = (
        await client.call_tool("ok_store_reviews", {"store_id": 11009, "sort": "LowestRate", "page_size": 5})
    ).structured_content
    assert api.params() == {"PageIndex": "1", "PageSize": "5", "Sort": "LowestRate"}
    assert out["total"] == 13 and len(out["reviews"]) == 5
    assert out["reviews"][0] == {
        "name": "نیکا",
        "rating": 1,
        "comment": "دوتا از محصولات تاریخ انقاشون\u200cتموم شده تماس گرفتیم با سوپری جواب ندادن",
        "date": "2026-09-24",
        "disliked": ["مشکل تاریخ انقضا"],
    }
    assert out["reviews"][1]["name"] is None  # "کاربر اکالا" = anonymous

    result = await client.call_tool("ok_store_reviews", {"store_id": 11009, "sort": "Oldest"})
    assert result.is_error and len(api.calls) == 1  # rejected before calling (the API answers an empty 400)
