import pytest
from conftest import POINT, fixture

pytestmark = pytest.mark.anyio

NEARBY = "/api/opex/v4/stores/nearby"
OFFERS = "/api/carousel/v4/offers"
OPEN = ["11009", "52299", "55428", "4957"]  # nearby.json minus the closed store


async def test_ok_deals_home(client, api):
    api[NEARBY] = fixture("nearby.json")
    api[OFFERS] = fixture("carousels_home.json")
    out = (await client.call_tool("ok_deals", {**POINT, "per_row": 2})).structured_content
    assert api.calls[1].url.params.get_list("storeIds") == OPEN and api.params()["pageType"] == "HomePage"
    assert [r["carousel_id"] for r in out["rows"]] == [110523, 112192, 111175]
    assert {k: v for k, v in out["rows"][1].items() if k != "products"} == {
        "carousel_id": 112192,
        "title": "زنگ تفریح",
        "subtitle": "آنلاین و آسان",
        "ends": "2026-10-22T23:59:59Z",
        "countdown": False,
        "brand_row": False,
        "store_count": 24,
    }
    assert out["rows"][1]["products"][0] == {
        "id": 2213,
        "name": "شیر کاکائو پرچرب 2% چربی میهن 200 میلی لیتری",
        "store_id": 11009,
        "store_name": "حمید",
        "final_price": 51750,
        "price": 69000,
        "discount_pct": 25,
        "in_stock": True,
        "max_order": 1,
        "badges": ["kalabarg"],
    }
    assert len(out["rows"][0]["products"]) == 2 and "top_discounts" not in out


async def test_ok_deals_store(client, api):
    api[OFFERS] = fixture("carousels_store.json")
    api["/api/carousel/v4/offers/highest-discount"] = fixture("highest.json")
    out = (await client.call_tool("ok_deals", {**POINT, "store_id": 11009})).structured_content
    assert (
        api.calls[0].url.params.get_list("storeIds") == ["11009"] and api.calls[0].url.params["pageType"] == "storepage"
    )
    assert api.params() == {"storeId": "11009", "take": "10", "page": "1"}
    assert [r["title"] for r in out["rows"]] == ["زنگ تفریح", "خرید با کالابرگ"]
    top = out["top_discounts"]
    assert top["total"] == 268 and [p["discount_pct"] for p in top["products"]] == [35, 35, 35]
    assert top["products"][0]["id"] == 269658 and top["products"][0]["final_price"] == 156000


async def test_ok_deals_nothing_open(client, api):
    api[NEARBY] = fixture("nearby_out.json")
    result = await client.call_tool("ok_deals", POINT)
    assert result.is_error and "No open Okala store" in result.content[0].text and len(api.calls) == 1


async def test_ok_offer_multi_store(client, api):
    api[NEARBY] = fixture("nearby.json")
    api[OFFERS + "/multi-store"] = fixture("multi.json")
    out = (await client.call_tool("ok_offer", {"carousel_id": 112192, **POINT, "sort": "cheapest"})).structured_content
    params = api.calls[1].url.params
    assert params["carouselId"] == "112192" and params.get_list("StoreIds") == OPEN
    assert out["title"] == "زنگ تفریح" and out["total"] == 9
    prices = [p["final_price"] for p in out["products"]]
    assert prices == sorted(prices) and prices[0] == 51750
    assert out["stores"][0] == {
        "store_id": 11009,
        "store_name": "حمید",
        "product_count": 11,
        "min_price": 31500,
        "max_price": 148750,
    }

    out = (await client.call_tool("ok_offer", {"carousel_id": 112192, **POINT, "page": 2})).structured_content
    assert out["products"] == [] and out["page"] == 2


async def test_ok_offer_single_store(client, api):
    api[OFFERS + "/single-store"] = fixture("single.json")
    out = (
        await client.call_tool("ok_offer", {"carousel_id": 112192, **POINT, "store_id": 11009, "sort": "cheapest"})
    ).structured_content
    assert api.params() == {
        "carouselId": "112192",
        "storeId": "11009",
        "Sort": "0",
        "Page": "1",
        "Take": "20",
        "excludeShoppingCard": "true",
    }
    assert out["total"] == 11 and [p["id"] for p in out["products"]] == [269711, 2213, 2215]
    assert "stores" not in out

    await client.call_tool("ok_offer", {"carousel_id": 112192, **POINT, "store_id": 11009})
    assert "Sort" not in api.params()  # default = the row's own order


async def test_ok_offer_unknown(client, api):
    api[OFFERS + "/single-store"] = {"carousel": None, "brands": [], "entities": [], "totalCount": 0, "success": True}
    result = await client.call_tool("ok_offer", {"carousel_id": 1, **POINT, "store_id": 11009})
    assert result.is_error and "No deal row 1" in result.content[0].text
