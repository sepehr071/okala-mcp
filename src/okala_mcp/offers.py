"""Deals: the home / store-page deal rows (carousels), one store's biggest discounts, one deal row in full."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from pydantic import Field

from .catalog import item
from .http import ApiError, api
from .registry import tool
from .stores import Lat, Lon, open_store_ids, toman

PAGE_SIZE = 20
SINGLE_SORTS = {"default": None, "cheapest": 0, "most_expensive": 1, "discount": 2}
CarouselId = Annotated[int, Field(ge=1, le=2_147_483_647, description="Deal row id (carousel_id) from ok_deals.")]
OptStore = Annotated[int | None, Field(ge=1, le=2_147_483_647, description="Store id from ok_stores, e.g. 11009.")]


def _row(c: dict[str, Any], per_row: int) -> dict[str, Any]:
    return {
        "carousel_id": c.get("id"),
        "title": c.get("title"),
        **({"subtitle": c.get("subTitle") or c.get("tag")} if c.get("subTitle") or c.get("tag") else {}),
        "ends": c.get("endTime"),
        "countdown": c.get("carouselType") == "WithCountDown",
        "brand_row": bool(c.get("brandIds")),
        "store_count": len(c.get("storeIds") or []),
        "products": [item(p, store=True) for p in (c.get("products") or [])[:per_row]],
    }


@tool("Current deals")
async def ok_deals(
    lat: Lat,
    lon: Lon,
    store_id: OptStore = None,
    per_row: Annotated[int, Field(ge=1, le=12, description="Products to show per deal row.")] = 3,
) -> dict[str, Any]:
    """Deal rows running now, with their end time (UTC) and top products (Toman, discount, store).

    Without store_id: the home-page rows across every open store that delivers to the point. With
    store_id: that store's rows plus its biggest discounts. Open a whole row with ok_offer(carousel_id).
    """
    store_ids = [store_id] if store_id else await open_store_ids(lat, lon)
    offers = api(
        "/carousel/v4/offers",
        {"pageType": "storepage" if store_id else "HomePage", "storeIds": store_ids, "latitude": lat, "longitude": lon},
    )
    if store_id:
        top_call = api("/carousel/v4/offers/highest-discount", {"storeId": store_id, "take": 10, "page": 1})
        body, top = await asyncio.gather(offers, top_call)
    else:
        body = await offers
    out: dict[str, Any] = {"rows": [_row(c, per_row) for c in body.get("carousels") or []]}
    if store_id:
        out["top_discounts"] = {
            "total": top.get("totalCount") or 0,
            "products": [item(p) for p in top.get("entities") or []],
        }
    return out


@tool("One deal row in full")
async def ok_offer(
    carousel_id: CarouselId,
    lat: Lat,
    lon: Lon,
    store_id: OptStore = None,
    sort: Annotated[
        Literal["default", "cheapest", "most_expensive", "discount"],
        Field(description="default (row order), cheapest / most_expensive (price to pay), discount (biggest % off)."),
    ] = "default",
    page: Annotated[int, Field(ge=1, le=100, description=f"Page number, from 1 ({PAGE_SIZE} products per page).")] = 1,
) -> dict[str, Any]:
    """Every product of one deal row (from ok_deals), Toman. Without store_id: across all open stores that
    deliver to the point, with each store's product count and price range; with store_id: that store only.
    """
    if store_id:
        body = await api(
            "/carousel/v4/offers/single-store",
            {
                "carouselId": carousel_id,
                "storeId": store_id,
                "Sort": SINGLE_SORTS[sort],
                "Page": page,
                "Take": PAGE_SIZE,
                "excludeShoppingCard": "true",
            },
        )
        products = [item(p, store=True) for p in body.get("entities") or []]
        total, stores = body.get("totalCount") or 0, None
    else:
        body = await api(
            "/carousel/v4/offers/multi-store",
            {"carouselId": carousel_id, "StoreIds": await open_store_ids(lat, lon), "latitude": lat, "longitude": lon},
        )
        entities = body.get("entities") or []
        # ponytail: each store lists at most 10 row products here; ok_offer(store_id=...) has the rest.
        products = [item(p, store=True) for e in entities for p in e.get("products") or []]
        if sort == "cheapest":
            products.sort(key=lambda r: r["final_price"] or 0)
        elif sort == "most_expensive":
            products.sort(key=lambda r: -(r["final_price"] or 0))
        elif sort == "discount":
            products.sort(key=lambda r: -r["discount_pct"])
        total = len(products)
        products = products[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        stores = [
            {
                "store_id": e.get("storeId"),
                "store_name": e.get("storeName"),
                "product_count": e.get("totalCount"),
                "min_price": toman(e.get("limitMinPrice")),
                "max_price": toman(e.get("limitMaxPrice")),
            }
            for e in entities
        ]
    carousel = body.get("carousel")
    if not carousel and not products:
        raise ApiError(f"No deal row {carousel_id} here (expired or unknown). Get current rows from ok_deals.")
    out: dict[str, Any] = {
        "carousel_id": carousel_id,
        "title": (carousel or {}).get("title"),
        "total": total,
        "page": page,
        "products": products,
    }
    if stores is not None:
        out["stores"] = stores
    return out
