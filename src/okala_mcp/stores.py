"""Location and stores: geocoding, stores that deliver to a point, one store's details, slots and reviews."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import ApiError, api
from .registry import tool

Lat = Annotated[float, Field(ge=25, le=40, description="Latitude from ok_locate, e.g. 35.7372.")]
Lon = Annotated[float, Field(ge=44, le=64, description="Longitude from ok_locate, e.g. 51.4154.")]
StoreId = Annotated[int, Field(ge=1, le=2_147_483_647, description="Store id from ok_stores, e.g. 11009.")]

STORE_TYPES = ("SuperMarket", "Bakery", "FruitMarket", "ChainStore", "Opack")
TEHRAN = timezone(timedelta(hours=3, minutes=30))  # Iran has had no DST since 2022
_ARABIC = str.maketrans(
    {"ي": "ی", "ك": "ک", "\u200c": " ", **{c: str(i % 10) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩")}}
)

_cities: list[dict[str, Any]] = []


def toman(rial: Any) -> int | None:
    """API money is Rial; tools speak Toman. 0 / missing -> None for prices."""
    return int(rial) // 10 if rial else None


def fee(rial: Any) -> int:
    """A store fee in Toman; 0 stays 0 (free)."""
    return int(rial or 0) // 10


def norm(text: str | None) -> str:
    """Lower-case Persian text with Arabic letters and digits unified and ZWNJ / punctuation as spaces."""
    text = (text or "").translate(_ARABIC).lower()
    return " ".join(re.sub(r"[^\w.%]+", " ", text).split())


async def nearby(lat: float, lon: float, store_type: str | None = None) -> dict[str, Any]:
    """opex v4 nearby: every store that delivers to the point (closed ones included)."""
    body = await api("/opex/v4/stores/nearby", {"latitude": lat, "longitude": lon, "storeTypes": store_type})
    return body.get("data") or {}


async def open_store_ids(lat: float, lon: float) -> list[int]:
    stores = (await nearby(lat, lon)).get("stores") or []
    ids = [s["storeId"] for s in stores if not (s.get("earliestSlot") or {}).get("isClosed")]
    if not ids:
        raise ApiError("No open Okala store delivers to this point right now. Check the point with ok_stores.")
    return ids


def store_fees(s: dict[str, Any]) -> dict[str, Any]:
    """Fees of a nearby store, Toman."""
    return {
        "service_fee": fee(s.get("operationPrice")),
        "packaging_fee": fee(s.get("packagingPrice")),
        "delivery_fee": fee(s.get("deliveryPrice")),
    }


def _slot(slot: dict[str, Any] | None, times: bool = True) -> dict[str, Any]:
    """Earliest slot: the caption, plus ISO from / to unless `times` is off (the caption already says it)."""
    slot = slot or {}
    out = {"slot": slot.get("caption")}
    return out | {"slot_from": slot.get("from"), "slot_to": slot.get("to")} if times else out


@tool("Find a delivery address")
async def ok_locate(
    address: Annotated[
        str,
        Field(
            min_length=2, max_length=200, description="Address or place in Persian, e.g. 'میدان ونک' or 'یوسف آباد'."
        ),
    ],
    city: Annotated[
        str, Field(min_length=2, max_length=40, description="City name in Persian, e.g. 'تهران', 'مشهد'.")
    ] = "تهران",
) -> dict[str, Any]:
    """Turn an address into coordinates (lat, lon) for every other tool, plus the street address of the best hit.

    Use first when the user gives an address or place name; then pass the chosen candidate's lat / lon to
    ok_stores, ok_search, ok_compare or ok_deals. Candidates can mix similar names: check `description`.
    """
    global _cities
    if not _cities:
        _cities = (await api("/voyager/C/Location/GetCitiesForReact")).get("data") or []
    wanted = norm(city)
    match = next((c for c in _cities if norm(c.get("name")) == wanted), None)
    if match is None:
        near = [c["name"] for c in _cities if wanted in norm(c.get("name")) or norm(c.get("name")) in wanted]
        names = near or sorted(c["name"] for c in _cities)
        raise ApiError(f"Okala does not list the city '{city}'. Supported city names: {', '.join(names)}")
    body = await api("/parsimap/forward", {"searchText": address, "district": match["name"], "onlyInDistrict": "false"})
    results = (body.get("results") or []) if body.get("status") == "OK" else []  # ParsiMap: always HTTP 200
    if not results:
        raise ApiError(f"No place matches '{address}' in {match['name']}. Try a street, square or district name.")
    candidates = []
    for r in results[:8]:
        geo = r.get("geo_location") or {}
        center = geo.get("center") or {}
        candidates.append(
            {
                "title": geo.get("title"),
                "description": r.get("description"),
                "kind": geo.get("category"),
                "lat": center.get("lat"),
                "lon": center.get("lng"),
            }
        )
    top = candidates[0]
    rev = await api("/parsimap/reverse", {"location": f"{top['lon']},{top['lat']}", "plate": "true"})
    return {
        "city": match["name"],
        "city_id": match.get("id"),
        "candidates": candidates,
        "top_address": {
            "address": rev.get("address"),
            "city": rev.get("subdivision_prefix"),
            "plate": rev.get("plate"),
        }
        if rev.get("status") == "OK"
        else None,
    }


@tool("Stores that deliver to a point")
async def ok_stores(
    lat: Lat,
    lon: Lon,
    store_type: Annotated[
        Literal["SuperMarket", "Bakery", "FruitMarket", "ChainStore", "Opack"] | None,
        Field(description="Only one kind: SuperMarket, Bakery, FruitMarket, ChainStore (Ofogh, HyperFamily) or Opack."),
    ] = None,
    open_only: Annotated[bool, Field(description="Leave out stores that are closed / cannot deliver now.")] = True,
    limit: Annotated[int, Field(ge=1, le=100, description="Max stores to return, in the site's order.")] = 20,
) -> dict[str, Any]:
    """Okala stores that deliver to a point, in the site's order: id, type, rating, distance, earliest
    slot and the fees added to every order (service, packaging, delivery; Toman).

    Use after ok_locate. Pass store ids to ok_store (minimum order, slots), ok_store_products, ok_product
    and ok_deals; to compare one product across stores use ok_compare. `count` = matches before `limit`.
    """
    data = await nearby(lat, lon, store_type)
    stores = data.get("stores") or []
    if not stores:
        return {
            "city_id": data.get("cityId") or 0,
            "count": 0,
            "stores": [],
            "message": "No Okala store delivers to this point"
            + (f" for type {store_type}." if store_type and data.get("cityId") else ". Okala does not cover it."),
        }
    out = []
    for s in stores:
        closed = bool((s.get("earliestSlot") or {}).get("isClosed"))
        if open_only and closed:
            continue
        row = {
            "store_id": s["storeId"],
            "name": s.get("storeName"),
            "type": s.get("storeCategoryType"),
            "rating": s.get("rate"),
            "rating_count": s.get("csatCount"),
            "distance_km": round(s.get("distance") or 0, 2),
            **_slot(s.get("earliestSlot"), times=False),  # ok_store has the ISO times
            "is_closed": closed,
            **store_fees(s),
            "delivery_provider": (s.get("deliveryProvider") or {}).get("description"),
            "labels": [lb.get("title") for lb in s.get("storeLabels") or [] if lb.get("title")],
        }
        out.append({k: v for k, v in row.items() if v not in (None, [])})  # drop empty fields
    return {
        "city_id": data.get("cityId"),
        "count": len(out),
        "types": {
            f.get("title"): f.get("storeCount") for f in (data.get("filters") or {}).get("storeTypeFilters") or []
        },
        "stores": out[:limit],
    }


@tool("Store details and delivery slots")
async def ok_store(
    store_id: StoreId,
    lat: Lat,
    lon: Lon,
    all_slots: Annotated[bool, Field(description="Also list full slots with an available flag.")] = False,
) -> dict[str, Any]:
    """One store as seen from the delivery point: whether it delivers there now, minimum order (Toman),
    rating, distance and location, the earliest slot, and every still-open delivery slot of the next days
    (Tehran local time).

    Use before recommending a store: the basket must reach `minimum_order`. Fees per order are in
    ok_stores; written reviews in ok_store_reviews.
    """
    body = await api(
        "/opex/v2/stores/details",
        {"Latitude": lat, "longitude": lon, "StoreId": store_id, "customerSegments": "null"},
    )
    d = body.get("data") or {}
    if not d.get("isExist") and not d.get("storeName"):
        raise ApiError(f"No Okala store with id {store_id}. Get store ids from ok_stores.")
    schedule, reviews = await asyncio.gather(
        api("/voyager/Order/GetActiveSchedule/1", {"storeId": store_id, "isMsBasketEnable": "true"}),
        api(f"/review/v1/stores/{store_id}/reviews", {"PageIndex": 1, "PageSize": 1}),
    )
    return {
        "store_id": d.get("storeId"),
        "name": d.get("storeName"),
        "type": d.get("storeCategoryName") or d.get("storeType"),
        "serves_this_point": bool(d.get("isActive") and d.get("isExist") and d.get("isServes")),
        "status": d.get("storeStatusMessage"),
        "minimum_order": fee(d.get("minimumOrder")),
        "rating": d.get("rate"),
        "rating_count": d.get("csatCount"),
        "review_count": ((reviews or {}).get("data") or {}).get("totalCount") or 0,
        "distance_km": round(d.get("distance") or 0, 2),
        "location": {"lat": d.get("storeLat"), "lon": d.get("storeLng")},
        "address": d.get("storeAddress"),
        **_slot(d.get("earliestSlot")),
        "is_closed": bool((d.get("earliestSlot") or {}).get("isClosed")),
        "entrance_delivery_only": bool(d.get("isBuildingEntranceDelivery")),
        "labels": [lb.get("title") for lb in d.get("storeLabels") or [] if lb.get("title")],
        "delivery_days": _days((schedule or {}).get("data") or {}, all_slots),
        "url": f"https://www.okala.com/store/{store_id}",
    }


def _days(data: dict[str, Any], all_slots: bool = False) -> list[dict[str, Any]]:
    """voyager schedule -> [{date, weekday, slots}]. Its `date` fields are Jalali in ISO shape: use the epochs."""
    days = []
    for part in data.get("partItems") or []:
        for day in part.get("deliverTimes") or []:
            slots = []
            for r in day.get("ranges") or []:
                start = datetime.fromtimestamp(r["startEpoch"], TEHRAN)
                end = datetime.fromtimestamp(r["endEpoch"], TEHRAN)
                if not r.get("isActive") and not all_slots:  # full slots add nothing to a delivery plan
                    continue
                slot: dict[str, Any] = {"time": f"{start:%H:%M}-{end:%H:%M}"}
                if all_slots:
                    slot["available"] = bool(r.get("isActive"))
                if r.get("description"):
                    slot["note"] = r["description"]
                if r.get("cost"):
                    slot["cost"] = fee(r["cost"])
                slots.append(slot)
            first = day.get("dateEpoch") or (day.get("ranges") or [{}])[0].get("startEpoch")
            days.append(
                {
                    "date": f"{datetime.fromtimestamp(first, TEHRAN):%Y-%m-%d}" if first else None,
                    "weekday": (day.get("dateStr") or "").split("\n")[0] or None,
                    "available_slots": sum(s.get("available", True) for s in slots),
                    "slots": slots,
                }
            )
    return days


def clip(text: str | None, limit: int = 400, full: bool = False) -> str | None:
    """Untrusted review text, cut to `limit` characters with a marker (unless `full`)."""
    text = (text or "").strip()
    return (text[:limit] + "… [cut]" if len(text) > limit and not full else text) or None


@tool("Store reviews")
async def ok_store_reviews(
    store_id: StoreId,
    sort: Annotated[
        Literal["Newest", "HighestRate", "LowestRate"], Field(description="Newest first, or by star rating.")
    ] = "Newest",
    page: Annotated[int, Field(ge=1, le=500, description="Page number, from 1.")] = 1,
    page_size: Annotated[int, Field(ge=1, le=50, description="Reviews per page.")] = 10,
    full_text: Annotated[bool, Field(description="Return whole comments (default: cut at 400 characters).")] = False,
) -> dict[str, Any]:
    """Read customers' reviews of a store: 1-5 stars, comment, date and the reasons they ticked
    (liked: product variety, ...; disliked: expiry date problems, ...).

    Use as a quality check before recommending a store; LowestRate shows the complaints first.
    The average rating and rating count are in ok_stores / ok_store.
    """
    body = await api(f"/review/v1/stores/{store_id}/reviews", {"PageIndex": page, "PageSize": page_size, "Sort": sort})
    data = body.get("data") or {}
    return {
        "total": data.get("totalCount") or 0,
        "page": page,
        "reviews": [
            {
                "name": r.get("reviewerName") if r.get("hasName") else None,
                "rating": r.get("vendorRate"),
                "comment": clip(r.get("comment"), full=full_text),
                "date": (r.get("submittedAt") or "")[:10] or None,
                **{
                    key: titles
                    for key, kind in (("liked", "Like"), ("disliked", "Dislike"))
                    if (titles := [x.get("title") for x in r.get("reasons") or [] if x.get("reasonType") == kind])
                },
            }
            for r in data.get("items") or []
        ],
    }
