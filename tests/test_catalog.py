import httpx
import pytest
from conftest import LAT, LON, POINT, fixture

pytestmark = pytest.mark.anyio

CATS = "/api/unicorn/v3/categories/nearby"
NEARBY_PRODUCTS = "/api/unicorn/v2/products/nearby"
STORE_PRODUCTS = "/api/unicorn/v2/products/store/11009"
PDP = "/api/Unicorn/v1/catalog/pdp"
POPULAR = "/api/azarakhsh/v1/Brand/GetPopularBrands"


async def test_ok_categories(client, api):
    api[CATS] = fixture("cats_nearby.json")
    out = (await client.call_tool("ok_categories", {**POINT, "depth": 1})).structured_content
    assert api.params() == {"lat": str(LAT), "lon": str(LON)}
    assert [c["slug"] for c in out["categories"]] == [
        "kalabarg",
        "refreshments",
        "dairy-products",
        "beverages",
        "canned-ready-food",
    ]
    assert "children" not in out["categories"][0]

    api[CATS] = fixture("cats_store.json")
    out = (await client.call_tool("ok_categories", {"store_id": 11009, "depth": 3})).structured_content
    assert api.params() == {"sId": "11009", "ch": "false"}
    dairy = next(c for c in out["categories"] if c["slug"] == "dairy-products")
    milk = next(c for c in dairy["children"] if c["slug"] == "milk")
    assert milk["id"] == 1484 and milk["name"] == "شیر"
    assert [c["slug"] for c in milk["children"]] == ["full-fat-milk", "low-fat-milk"]


async def test_ok_categories_needs_one_scope(client, api):
    for args in ({}, {"lat": LAT}, {**POINT, "store_id": 11009}):
        result = await client.call_tool("ok_categories", args)
        assert result.is_error and "either lat + lon" in result.content[0].text
    assert not api.calls


async def test_ok_search(client, api):
    api[CATS] = fixture("cats_nearby.json")
    api[NEARBY_PRODUCTS] = fixture("products_milk.json")
    out = (await client.call_tool("ok_search", {"query": "شير كم چرب", **POINT})).structured_content  # Arabic ي / ك
    assert out["categories"][0] == {
        "id": 1605,
        "slug": "low-fat-milk",
        "name": "شیر کم چرب و نیم چرب",
        "level": 3,
        "used": True,
    }
    assert not any(c["used"] for c in out["categories"][1:])  # weaker matches are only suggested
    assert len(api.calls) == 2 and api.params() == {"slug": "low-fat-milk", "lat": str(LAT), "lon": str(LON)}
    # All three words first (cheapest store first), then partial matches; the closed store 53905 is left out.
    assert [(p["id"], p["store_id"], p["final_price"]) for p in out["products"]] == [
        (716, 11009, 135000),
        (716, 52299, 139125),
        (716, 4957, 149555),
        (289271, 4957, 144200),
        (12043, 4957, 157900),
    ]
    assert out["products"][0] == {
        "id": 716,
        "name": "شير كم چرب 1.5% چربی میهن 1 لیتری",
        "store_id": 11009,
        "store_name": "حمید",
        "final_price": 135000,
        "price": 150000,
        "discount_pct": 10,
        "in_stock": True,
        "max_order": 1,
        "badges": ["kalabarg"],
    }
    assert out["total"] == 5 and out["brands"][0] == {"brand_id": 12, "slug": "mihan", "name": "میهن"}


async def test_ok_search_ties_and_misses(client, api):
    api[CATS] = fixture("cats_nearby.json")
    api[NEARBY_PRODUCTS] = fixture("products_milk.json")
    out = (await client.call_tool("ok_search", {"query": "تن ماهی", **POINT})).structured_content
    assert [c["slug"] for c in out["categories"] if c["used"]] == ["canned-tuna"]  # 'تن\u200cماهی' with a ZWNJ
    assert api.params()["slug"] == "canned-tuna"

    out = (await client.call_tool("ok_search", {"query": "شیر", **POINT, "limit": 2})).structured_content
    assert [c["slug"] for c in out["categories"] if c["used"]] == ["milk"]  # exact name beats 'شیر پرچرب'
    assert len(out["products"]) == 2

    cats = fixture("cats_nearby.json")
    cats["data"]["categories"][1]["children"].append({"id": 9999, "latinName": "milk-box", "name": "شیر", "level": 2})
    api[CATS] = cats
    out = (await client.call_tool("ok_search", {"query": "شیر", **POINT})).structured_content
    assert [c["slug"] for c in out["categories"] if c["used"]] == ["milk", "milk-box"]  # a tie fetches both
    assert out["total"] == 5  # the same store + product from both replies counts once

    calls = len(api.calls)
    out = (await client.call_tool("ok_search", {"query": "لپ تاپ", **POINT})).structured_content
    assert out["products"] == [] and "ok_categories" in out["message"] and len(api.calls) == calls + 1


async def test_ok_search_outside_coverage(client, api):
    api[CATS] = {"data": None, "success": False, "tags": [{"key": "stores", "value": 0}], "errorMessage": None}
    result = await client.call_tool("ok_search", {"query": "شیر", "lat": 30.0, "lon": 60.0})
    assert result.is_error and "no Okala store delivers to this point" in result.content[0].text


async def test_ok_compare(client, api):
    api["/api/opex/v4/stores/nearby"] = fixture("nearby.json")
    api[NEARBY_PRODUCTS] = fixture("products_milk.json")
    out = (await client.call_tool("ok_compare", {**POINT, "category_slug": "milk"})).structured_content
    assert api.params() == {"slug": "milk", "lat": str(LAT), "lon": str(LON)}
    assert out["product_count"] == 3
    milk = out["products"][0]  # sold by the most stores
    assert (milk["id"], milk["store_count"], milk["cheapest_store_id"], milk["saving_vs_dearest"]) == (
        716,
        3,
        11009,
        14555,
    )
    assert milk["offers"] == [
        {"store_id": 11009, "final_price": 135000, "discount_pct": 10, "price": 150000},
        {"store_id": 52299, "final_price": 139125, "discount_pct": 12, "price": 159000},
        {"store_id": 4957, "final_price": 149555, "discount_pct": 5, "price": 159000},
    ]
    assert [p["id"] for p in out["products"]] == [716, 289271, 12043]
    assert sorted(out["stores"]) == ["11009", "4957", "52299"]  # the closed store 53905 is left out
    assert out["stores"]["11009"] == {
        "name": "حمید",
        "distance_km": 1.1,
        "rating": 4.1,
        "slot": "امروز - 07 تا 08",
        "service_fee": 10500,
        "packaging_fee": 3000,
        "delivery_fee": 0,
    }

    await client.call_tool("ok_compare", {**POINT, "brand_id": 1, "limit": 1})
    assert api.params() == {"BrandIds": "1", "lat": str(LAT), "lon": str(LON)}

    result = await client.call_tool("ok_compare", POINT)
    assert result.is_error and "category_slug" in result.content[0].text


async def test_ok_store_products(client, api):
    api[STORE_PRODUCTS] = fixture("store_dairy.json")
    args = {"store_id": 11009, "category_slug": "dairy-products", "sort": "cheapest", "name_contains": "شیر"}
    out = (await client.call_tool("ok_store_products", args)).structured_content
    assert api.params() == {"slug": "dairy-products", "Sort": "0"}
    assert out["total"] == 5  # 'شیر' in the name and in stock
    assert [p["id"] for p in out["products"]] == [181327, 3137, 194299, 2123, 178095]
    assert out["products"][1] == {
        "id": 3137,
        "name": "شیر پر چرب 3.2% چربی میهن 200 میلی لیتری",
        "final_price": 41481,
        "price": 41900,
        "discount_pct": 1,
        "in_stock": True,
        "max_order": 5,
        "badges": ["kalabarg"],
    }

    out = (
        await client.call_tool("ok_store_products", {**args, "name_contains": "شیر کم", "in_stock_only": False})
    ).structured_content
    assert [p["id"] for p in out["products"]] == [181327, 178095]

    out = (
        await client.call_tool(
            "ok_store_products", {**args, "name_contains": None, "in_stock_only": False, "limit": 2, "offset": 8}
        )
    ).structured_content
    assert out["total"] == 10 and [p["in_stock"] for p in out["products"]] == [False, False]

    await client.call_tool("ok_store_products", {**args, "sort": "default"})
    assert api.params()["Sort"] == "3"


async def test_ok_store_products_unknown_slug(client, api):
    api[STORE_PRODUCTS] = {"data": None, "success": False, "tags": [{"key": "nullCategory", "value": True}]}
    result = await client.call_tool("ok_store_products", {"store_id": 11009, "category_slug": "nope"})
    assert result.is_error and "unknown category slug" in result.content[0].text


async def test_ok_product(client, api):
    api[PDP] = fixture("pdp.json")
    out = (await client.call_tool("ok_product", {"product_id": 269658, "store_id": 11009})).structured_content
    assert api.params() == {"sId": "11009", "pId": "269658"}
    assert out == {
        "id": 269658,
        "name": "پفک پاف کینوا پنیری کینوفیت 45 گرمی",
        "final_price": 156000,  # 1,560,000 Rial, shown on the site as ۱۵۶٬۰۰۰ تومان
        "price": 240000,
        "discount_pct": 35,
        "in_stock": True,
        "max_order": 1,
        "store_id": 11009,
        "store_name": "حمید",
        "sold_here": True,
        "quantity": 10,
        "brand": "کینوفیت",
        "brand_slug": "quinofit",
        "category": "پفک و اسنک",
        "category_slug": "snacks",
        "is_bundle": False,
        "description": None,  # same as the name
        "images": [
            "https://asset.okala.com/unsigned/rs:fill/size:0:0/plain/s3://cdn/product/4f101c09-d934-4ad7-9ac5-043c1aba60ca.jpg"
        ],
        "url": "https://www.okala.com/product/269658?storeId=11009",
    }


async def test_ok_product_not_sold_and_unknown(client, api):
    pdp = fixture("pdp.json") | {"supplyStatus": 2, "okPrice": 0.0, "price": 0.0, "quantity": 0, "discountPercent": 0}
    api[PDP] = pdp
    out = (await client.call_tool("ok_product", {"product_id": 269658, "store_id": 52299})).structured_content
    assert (out["sold_here"], out["in_stock"], out["final_price"], out["price"]) == (False, False, None, None)

    problem = {"type": "https://tools.ietf.org/html/rfc9110#section-15.5.5", "title": "Not Found", "status": 404}
    api[PDP] = lambda r: httpx.Response(404, json=problem)
    result = await client.call_tool("ok_product", {"product_id": 999999999, "store_id": 11009})
    assert result.is_error and "Not found on okala.com (HTTP 404)" in result.content[0].text


async def test_ok_brands(client, api):
    api[POPULAR] = fixture("popular_brands.json")
    out = (await client.call_tool("ok_brands", {})).structured_content
    assert out["brands"][0] == {"brand_id": 276, "slug": "persil", "name": "پرسیل"} and len(out["brands"]) == 6

    out = (await client.call_tool("ok_brands", {"name": "كاله"})).structured_content  # Arabic ك
    assert out["brands"] == [{"brand_id": 1, "slug": "kalleh", "name": "کاله"}]
    out = (await client.call_tool("ok_brands", {"name": "مای لیدی"})).structured_content  # site writes a ZWNJ
    assert out["brands"][0]["slug"] == "my-lady"


async def test_ok_brands_slug_lookup(client, api):
    api[POPULAR] = []
    api["/api/azarakhsh/v1/Brand/brand-specific-page/kalleh"] = fixture("brand_kalleh.json")
    out = (await client.call_tool("ok_brands", {"name": "Kalleh"})).structured_content
    assert out["brands"] == [{"brand_id": 1, "slug": "kalleh", "name": "کاله"}]

    asp_result = {"Value": {"Code": "SYSTEM_EXCEPTION", "Message": "Brand"}, "StatusCode": 400}
    api["/api/azarakhsh/v1/Brand/brand-specific-page/nope"] = lambda r: httpx.Response(400, json=asp_result)
    result = await client.call_tool("ok_brands", {"name": "nope"})
    assert result.is_error and "No brand 'nope'" in result.content[0].text
    result = await client.call_tool("ok_brands", {"name": "برند ناشناس"})  # not a slug: no lookup
    assert result.is_error and api.calls[-1].url.path == POPULAR
