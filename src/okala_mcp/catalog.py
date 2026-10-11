"""Products: category tree, category-based search, cross-store price comparison, one store's shelf,
one product, brands."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import ApiError, api, logged_in
from .registry import tool
from .stores import Lat, Lon, StoreId, nearby, norm, store_fees, toman

Slug = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z0-9_-]{1,80}$",
        description="Category slug (latinName) from ok_categories / ok_search, e.g. 'milk', 'dairy-products'.",
    ),
]
OptLat = Annotated[float | None, Field(ge=25, le=40, description="Delivery point latitude, e.g. 35.7372.")]
OptLon = Annotated[float | None, Field(ge=44, le=64, description="Delivery point longitude, e.g. 51.4154.")]
InStock = Annotated[bool, Field(description="Only products that can be ordered now.")]

SORTS = {"cheapest": 0, "most_expensive": 1, "discount": 2, "default": 3}
GUEST_TIP = (
    "Guest search matches categories only. If the user wants exact search (brands, product names), offer ok_login."
)


def badges(raw: Any) -> list[str]:
    """`catalogBadges` is JSON text: '[{"name": "kalabarg", ...}]' -> ['kalabarg'] (kalabarg = government voucher)."""
    if not raw:
        return []
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return [b["name"] for b in items or [] if isinstance(b, dict) and b.get("name")]


def item(p: dict[str, Any], store: bool = False) -> dict[str, Any]:
    """One product row of any list, prices in Toman. supplyStatus 2 = the store does not sell it (prices 0)."""
    final, pct = toman(p.get("okPrice")), int(p.get("discountPercent") or 0)
    out: dict[str, Any] = {"id": p.get("id") or p.get("productId"), "name": (p.get("name") or "").strip()}
    if store:
        out |= {"store_id": p.get("storeId"), "store_name": p.get("storeName")}
    out |= {
        "final_price": final,
        "price": toman(p.get("price")) if pct else final,
        "discount_pct": pct,
        "in_stock": bool(p.get("hasQuantity", (p.get("quantity") or 0) > 0)) and p.get("supplyStatus") != 2,
        "max_order": p.get("maxOrderLimit"),
    }
    if b := badges(p.get("catalogBadges")):
        out["badges"] = b
    return out


async def category_tree(lat: float | None, lon: float | None, store_id: int | None = None) -> list[dict[str, Any]]:
    params = {"sId": store_id, "ch": "false"} if store_id else {"lat": lat, "lon": lon}
    body = await api("/unicorn/v3/categories/nearby", params)
    return (body.get("data") or {}).get("categories") or []


def _compact(nodes: list[dict[str, Any]], depth: int) -> list[dict[str, Any]]:
    out = []
    for n in nodes:
        node: dict[str, Any] = {"id": n.get("id"), "slug": n.get("latinName"), "name": n.get("name")}
        if depth > 1 and n.get("children"):
            node["children"] = _compact(n["children"], depth - 1)
        out.append(node)
    return out


def _flat(nodes: list[dict[str, Any]], seen: dict[int, dict[str, Any]] | None = None) -> dict[int, dict[str, Any]]:
    """Every node once (a node can sit under several parents, e.g. kalabarg repeats milk)."""
    seen = {} if seen is None else seen
    for n in nodes:
        seen.setdefault(n["id"], n)
        _flat(n.get("children") or [], seen)
    return seen


@tool("Category tree")
async def ok_categories(
    lat: OptLat = None,
    lon: OptLon = None,
    store_id: Annotated[int | None, Field(ge=1, description="Instead of a point: one store's categories.")] = None,
    depth: Annotated[
        int, Field(ge=1, le=3, description="Levels to return: 1 departments, 2 + sub-categories, 3 all.")
    ] = 2,
) -> dict[str, Any]:
    """Category tree (id, slug, Persian name, children) of everything sold near a point, or in one store.

    Use to find the slug for ok_compare / ok_store_products when ok_search's guess is off, or to show
    what a store sells. Level-1 slugs (departments, e.g. 'dairy-products') work in ok_store_products.
    """
    if (lat is None) != (lon is None) or (lat is None) == (store_id is None):
        raise ApiError("Pass either lat + lon (stores near a point) or store_id, not both.")
    tree = await category_tree(lat, lon, store_id)
    return {"categories": _compact(tree, depth)}


def _tokens(query: str) -> list[str]:
    return [t for t in norm(query).split() if len(t) > 1 or t.isdigit()]


def _score(tokens: list[str], name: str) -> tuple[int, int]:
    """(match score, extra words): a whole-word hit counts 2, a substring hit 1."""
    words = norm(name).split()
    joined = "".join(words)
    score = sum(2 if t in words else 1 if t in joined else 0 for t in tokens)
    return score, len(words) - sum(t in words for t in tokens)


def _closed(store: dict[str, Any]) -> bool:
    """Cross-store lists include stores that cannot deliver now; their prices are no use."""
    return bool((store.get("earliestSlot") or {}).get("isClosed"))


def _hits(tokens: list[str], name: str) -> int:
    text = norm(name)
    return sum(t in text for t in tokens)


@tool("Search products near a point")
async def ok_search(
    query: Annotated[
        str,
        Field(
            min_length=2,
            max_length=100,
            description="What to buy, in Persian, e.g. 'شیر کم چرب', 'پنیر لیقوان', 'تن ماهی'.",
        ),
    ],
    lat: OptLat = None,
    lon: OptLon = None,
    store_id: Annotated[
        int | None, Field(ge=1, description="Search inside this store only (needs login), e.g. 11009.")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=60, description="Max products to return.")] = 20,
) -> dict[str, Any]:
    """Find products by text, best matches first, then cheapest (Toman). `mode` tells which way ran:

    - "text" (when logged in with `okala-mcp login`): Okala's own search across all nearby open stores
      (lat + lon, up to 12 products per store), or inside one store (store_id). Matches brands and
      product names too ('نوتلا'). `suggestions` are Okala's related queries.
    - "category" (guest): Okala has no public text search, so the query words are matched against the
      nearby category names, the best category (two on a tie) is fetched across nearby open stores, and
      products whose names contain the query words come first. A pure brand or product name may find
      nothing: then pick a slug from ok_categories. store_id needs login; as a guest use
      ok_store_products with name_contains.

    Use for "how much is X near me". Then: ok_compare (same product in several stores with fees),
    ok_product (one product in one store).
    """
    tokens = _tokens(query)
    if not tokens:
        raise ApiError("The query has no searchable words. Use Persian product words, e.g. 'شیر'.")
    if store_id is None and (lat is None or lon is None):
        raise ApiError("Pass lat + lon (search near a point; ok_locate turns an address into them) or store_id.")
    if logged_in():
        return await _text_search(query, tokens, lat, lon, store_id, limit)
    if store_id is not None:
        raise ApiError(
            "Searching inside one store needs login (`okala-mcp login <phone>` in a terminal). "
            "As a guest use ok_store_products with a category slug and name_contains."
        )
    assert lat is not None and lon is not None
    nodes = _flat(await category_tree(lat, lon)).values()
    ranked = sorted(
        ((_score(tokens, n.get("name") or ""), n) for n in nodes),
        key=lambda x: (-x[0][0], x[0][1], x[1].get("level") or 0),
    )
    matched = [(s, n) for s, n in ranked if s[0] > 0][:6]
    if not matched:
        return {
            "query": query,
            "mode": "category",
            "categories": [],
            "products": [],
            "message": "No nearby category name matches the query. Pick a slug from ok_categories and use ok_compare.",
            "tip": GUEST_TIP,
        }
    # A second category only when it ties with the best (same name match), not a weaker or longer one.
    used = [n for s, n in matched[:2] if s == matched[0][0]]
    replies = await asyncio.gather(
        *(api("/unicorn/v2/products/nearby", {"slug": n["latinName"], "lat": lat, "lon": lon}) for n in used)
    )
    rows: dict[tuple[int, int], dict[str, Any]] = {}
    brands: dict[int, dict[str, Any]] = {}
    for reply in replies:
        for b in reply.get("brands") or []:
            brands.setdefault(
                b["legacyId"], {"brand_id": b["legacyId"], "slug": b.get("latinName"), "name": b.get("name")}
            )
        for group in reply.get("data") or []:
            store = group.get("store") or {}
            if _closed(store):
                continue
            for p in group.get("products") or []:
                p = {**p, "storeId": store.get("storeId"), "storeName": store.get("storeName")}
                rows.setdefault((p["storeId"], p["id"]), item(p, store=True))
    products = _ranked(tokens, rows.values())
    return {
        "query": query,
        "mode": "category",
        "categories": [
            {
                "id": n["id"],
                "slug": n.get("latinName"),
                "name": n.get("name"),
                "level": n.get("level"),
                "used": n in used,
            }
            for _, n in matched
        ],
        "total": len(products),
        "brands": list(brands.values())[:15],
        "products": products[:limit],
        "tip": GUEST_TIP,
    }


def _ranked(tokens: list[str], rows: Any) -> list[dict[str, Any]]:
    """Most query words in the name first, then in stock, then cheapest."""
    return sorted(
        rows,
        key=lambda r: (-_hits(tokens, r["name"]), not r["in_stock"], r["final_price"] is None, r["final_price"] or 0),
    )


async def _text_search(
    query: str, tokens: list[str], lat: float | None, lon: float | None, store_id: int | None, limit: int
) -> dict[str, Any]:
    """Okala's own search (login only): one store, or every nearby store (grouped per store, 12 each)."""
    rows: dict[tuple[int, int], dict[str, Any]] = {}
    if store_id is not None:
        reply = await api(f"/unicorn/v2/search/store/{store_id}", {"q": query, "s": SORTS["default"]})
        for p in reply.get("data") or []:
            rows.setdefault((store_id, p["id"]), item(p) | {"store_id": store_id})
        stores = 1
    else:
        reply = await api("/unicorn/v2/cumulative/search/nearby", {"q": query, "lat": lat, "lon": lon})
        groups = [g for g in reply.get("data") or [] if not _closed(g.get("store") or {})]
        for group in groups:
            store = group.get("store") or {}
            for p in group.get("products") or []:
                p = {**p, "storeId": store.get("storeId"), "storeName": store.get("storeName")}
                rows.setdefault((p["storeId"], p["id"]), item(p, store=True))
        stores = len(groups)
    products = _ranked(tokens, rows.values())
    return {
        "query": query,
        "mode": "text",
        "suggestions": (reply.get("suggestions") or [])[:9],
        "stores": stores,
        "total": len(products),
        "products": products[:limit],
    }


@tool("Compare prices across stores")
async def ok_compare(
    lat: Lat,
    lon: Lon,
    category_slug: Annotated[
        str | None,
        Field(
            pattern=r"^[A-Za-z0-9_-]{1,80}$", description="Category slug from ok_search / ok_categories, e.g. 'milk'."
        ),
    ] = None,
    brand_id: Annotated[
        int | None,
        Field(
            ge=1,
            description="Brand id from ok_brands / ok_search (e.g. 1 = Kalleh); with a slug = that brand in the category.",
        ),
    ] = None,
    in_stock_only: InStock = True,
    limit: Annotated[int, Field(ge=1, le=30, description="Max products to return.")] = 10,
) -> dict[str, Any]:
    """Which nearby store sells the same product cheapest. Fetches a category (and/or brand) from every store
    that delivers to the point and is open now, groups by product id (the same id is the same item in every store) and lists
    each store's price, cheapest first. Products sold by the most stores come first.

    `stores` holds each store's name, distance, earliest slot and fees: the true cost of an order is the
    items + service fee + packaging fee + delivery fee, and the store's minimum order applies (ok_store).
    Up to 12 products per store come back per call: narrow with a deeper slug or a brand_id.
    """
    if not category_slug and not brand_id:
        raise ApiError("Pass category_slug (from ok_search / ok_categories) or brand_id (from ok_brands).")
    near, body = await asyncio.gather(
        nearby(lat, lon),
        api("/unicorn/v2/products/nearby", {"slug": category_slug, "BrandIds": brand_id, "lat": lat, "lon": lon}),
    )
    fees = {s["storeId"]: s for s in near.get("stores") or []}
    groups: dict[int, dict[str, Any]] = {}
    stores: dict[int, dict[str, Any]] = {}
    for group in body.get("data") or []:
        store = group.get("store") or {}
        sid = store.get("storeId")
        if _closed(store):
            continue
        for p in group.get("products") or []:
            row = item(p)
            if row["final_price"] is None or (in_stock_only and not row["in_stock"]):
                continue
            g = groups.setdefault(row["id"], {"id": row["id"], "name": row["name"], "offers": []})
            offer = {"store_id": sid, "final_price": row["final_price"], "discount_pct": row["discount_pct"]}
            if row["discount_pct"]:
                offer["price"] = row["price"]
            if not row["in_stock"]:
                offer["in_stock"] = False
            g["offers"].append(offer)
            if sid not in stores:
                stores[sid] = {
                    "name": store.get("storeName"),
                    "distance_km": round(store.get("distance") or 0, 2),
                    "rating": store.get("rate"),
                    "slot": (store.get("earliestSlot") or {}).get("caption"),
                    **store_fees(fees.get(sid) or {"deliveryPrice": store.get("deliveryPrice")}),
                }
    ranked = sorted(groups.values(), key=lambda g: (-len(g["offers"]), min(o["final_price"] for o in g["offers"])))[
        :limit
    ]
    for g in ranked:
        g["offers"].sort(key=lambda o: o["final_price"])
        g["store_count"] = len(g["offers"])
        lo, hi = g["offers"][0]["final_price"], g["offers"][-1]["final_price"]
        g["cheapest_store_id"], g["saving_vs_dearest"] = g["offers"][0]["store_id"], hi - lo
    used = {o["store_id"] for g in ranked for o in g["offers"]}
    return {
        "product_count": len(groups),
        "products": ranked,
        "stores": {sid: s for sid, s in stores.items() if sid in used},
    }


@tool("Products of one store")
async def ok_store_products(
    store_id: StoreId,
    category_slug: Slug,
    sort: Annotated[
        Literal["cheapest", "most_expensive", "discount", "default"],
        Field(
            description="cheapest / most_expensive (price to pay), discount (biggest % off), default (site ranking)."
        ),
    ] = "default",
    name_contains: Annotated[
        str | None,
        Field(max_length=100, description="Keep only products whose name contains every word, e.g. 'کم چرب'."),
    ] = None,
    in_stock_only: InStock = True,
    limit: Annotated[int, Field(ge=1, le=100, description="Max products to return.")] = 50,
    offset: Annotated[int, Field(ge=0, le=5000, description="Skip this many matches (paging).")] = 0,
) -> dict[str, Any]:
    """A whole category of one store (the API returns all of it at once), sorted and optionally filtered by
    name, prices in Toman. `total` counts the matches before limit / offset.

    This is the way to search inside one store: pass a level-1 department slug (ok_categories with
    store_id, e.g. 'dairy-products') plus name_contains. Details of one product: ok_product.
    """
    body = await api(f"/unicorn/v2/products/store/{store_id}", {"slug": category_slug, "Sort": SORTS[sort]})
    rows = [item(p) for p in body.get("data") or []]
    if name_contains:
        words = _tokens(name_contains)
        rows = [r for r in rows if _hits(words, r["name"]) == len(words)]
    if in_stock_only:
        rows = [r for r in rows if r["in_stock"]]
    return {
        "store_id": store_id,
        "category": category_slug,
        "total": len(rows),
        "offset": offset,
        "products": rows[offset : offset + limit],
    }


@tool("Product details in a store")
async def ok_product(
    product_id: Annotated[int, Field(ge=1, le=2_147_483_647, description="Product id from any list, e.g. 269658.")],
    store_id: StoreId,
) -> dict[str, Any]:
    """One product in one store: price and discount (Toman), stock, max per order, brand, category,
    images and description. `sold_here: false` = this store does not sell it (try ok_compare).

    The product id is the same in every store; the price and stock are per store.
    """
    p = await api("/Unicorn/v1/catalog/pdp", {"sId": store_id, "pId": product_id})
    out = item(p)
    link = p.get("categoryWebLink") or ""
    description = (p.get("description") or "").strip()
    return {
        **out,
        "store_id": p.get("storeId"),
        "store_name": p.get("storeName"),
        "sold_here": p.get("supplyStatus") != 2,
        "quantity": p.get("quantity"),
        "brand": p.get("brandName"),
        "brand_slug": p.get("brandLatinName"),
        "category": p.get("categoryName"),
        "category_slug": link.rstrip("/").rsplit("/", 1)[-1] or None,
        "is_bundle": bool(p.get("isBundle")),
        "description": description[:1500] if description and description != out["name"] else None,
        "images": (p.get("images") or [p.get("imageUrl")])[:2],
        "url": f"https://www.okala.com/product/{product_id}?storeId={store_id}",
    }


@tool("Find brands")
async def ok_brands(
    name: Annotated[
        str | None,
        Field(
            max_length=60, description="Brand name in Persian or Latin, or its slug, e.g. 'کاله', 'kalleh', 'mihan'."
        ),
    ] = None,
) -> dict[str, Any]:
    """Brand ids for ok_compare (`brand_id`) and slugs. Okala publishes only a short list of featured brands;
    any other brand is looked up by its Latin slug (as in okala.com/brands/<slug>; ok_product returns
    brand_slug, ok_search returns the brands of its results with ids).
    """
    featured = await api("/azarakhsh/v1/Brand/GetPopularBrands")
    brands = [_brand(b) for b in featured or []]
    if not name:
        return {"brands": brands}
    wanted = norm(name)
    found = [b for b in brands if wanted in norm(b["name"]) or wanted in norm(b["slug"])]
    slug = name.strip().lower()
    if not found and re.fullmatch(r"[a-z0-9-]{2,60}", slug):
        try:
            found = [_brand(await api(f"/azarakhsh/v1/Brand/brand-specific-page/{slug}"))]
        except ApiError as e:
            if "HTTP 400" not in str(e):  # unknown slug is a 400 SYSTEM_EXCEPTION
                raise
    if not found:
        raise ApiError(
            f"No brand '{name}'. Use the Latin slug from okala.com/brands/<slug> or ok_product's brand_slug; "
            "ok_search lists the brands of its results with ids."
        )
    return {"brands": found}


def _brand(b: dict[str, Any]) -> dict[str, Any]:
    return {"brand_id": b.get("legacyId"), "slug": b.get("latinName"), "name": b.get("name")}
