"""Real-time repair shop lookup near case pickup, delivery, and route corridors."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import quote, urlencode
import urllib.request

_USER_AGENT = "KurtexMaintenanceWorkspace/1.0 (+https://kurtex.local)"
_CACHE_TTL_SECONDS = 900
_CACHE_MAX_ENTRIES = 128
_CACHE_LOCK = threading.Lock()
_RESULT_CACHE: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}

_US_STATE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z .'-]{1,30}),?\s+"
    r"(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|"
    r"MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|"
    r"AB|BC|MB|NB|NL|NS|ON|PE|QC|SK)\b(?:\s+(\d{5}))?",
    re.I,
)
_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_PLACEHOLDER_VALUES = {"", "-", "—", "--", "n/a", "na", "none", "null", "unknown", "tbd", "pending"}
_HEAVY_NAME_RE = re.compile(
    r"\b(truck|semi|diesel|trailer|reefer|thermo\s*king|carrier|freightliner|kenworth|"
    r"peterbilt|volvo|mack|international|navistar|cummins|detroit|paccar|speedco|loves|"
    r"love's|petro|ta\b|boss\b|fleet|wabash|utility|great\s*dane|towing|wrecker|"
    r"heavy\s*duty|roadside|mobile\s*repair|tire|tyre|michelin|bridgestone|goodyear|"
    r"southern\s*tire|pomp|commercial|axle|brake|alignment)\b",
    re.I,
)
_LIGHT_AUTO_BRAND_RE = re.compile(
    r"\b(jiffy\s*lube|valvoline|take\s*5|meineke|midas|pep\s*boys|autozone|"
    r"o'reilly|advance\s*auto|napa\s*auto|safelite|tint|detailing|car\s*wash)\b",
    re.I,
)

_ANCHOR_LABELS = {
    "pickup": "Near Pickup",
    "delivery": "Near Delivery",
    "corridor": "Along Route",
    "location": "Near Unit",
}


def clean_place_query(raw: Any) -> str:
    """Normalize a pickup/delivery/location field into a clean searchable place text."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if text.lower() in _PLACEHOLDER_VALUES:
        return ""
    # Strip parenthetical dock/appointment noise while keeping street/city/state info.
    text = re.sub(r"\((?:dock|gate|appt|pu|del|ref|po)[^)]*\)", " ", text, flags=re.I)
    text = re.sub(r"\b(?:dock|door|bay|pu|del|appt|ref)\s*#?\s*[a-z0-9-]+\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,;-")
    if text.lower() in _PLACEHOLDER_VALUES or len(text) < 2:
        return ""
    return text[:140]


def _city_state_fallback(cleaned: str) -> str:
    """Extract a clean 'City, ST' or ZIP fallback from verbose facility strings."""
    m = _US_STATE_RE.search(cleaned)
    if m:
        city = re.sub(r"^(?:at|near|in|jbs|meijer|walmart|target|plant|dc|yard)\b[:\s-]*", "", m.group(1), flags=re.I).strip(" ,.-")
        state = m.group(2).upper()
        zip_code = f" {m.group(3)}" if m.group(3) else ""
        if city:
            return f"{city}, {state}{zip_code}"
        return f"{state}{zip_code}".strip()
    zm = _ZIP_RE.search(cleaned)
    if zm:
        return zm.group(0)
    return ""


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in miles between two WGS84 points."""
    r = 3958.7613
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def _default_http_fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | str | None = None,
    timeout: float = 6.0,
) -> Any:
    """Execute a bounded JSON HTTP request via urllib."""
    req_headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    req = urllib.request.Request(url, data=payload, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _specialty_phrase(vehicle_type: str, systems: set[str]) -> str:
    """Build targeted commercial repair query terms without leaking case notes."""
    vtype = str(vehicle_type or "").strip().lower()
    if "reefer" in systems or vtype == "reefer":
        return "reefer trailer Thermo King Carrier refrigeration repair"
    if "towing" in systems:
        return "heavy duty semi truck towing wrecker service"
    if "tires" in systems:
        return "commercial semi truck tire repair road service"
    if "brakes" in systems or "air_suspension" in systems or vtype == "trailer":
        return "semi trailer truck brake suspension repair shop"
    if "engine_aftertreatment" in systems or "electrical" in systems or "drivetrain" in systems:
        return "heavy duty diesel semi truck repair shop"
    return "commercial semi truck and trailer repair shop"


def _build_anchors(pickup: str, delivery: str, location: str) -> list[dict[str, Any]]:
    """Build ordered pickup / delivery / fallback location anchors."""
    anchors: list[dict[str, Any]] = []
    seen_norm: set[str] = set()
    p_clean = clean_place_query(pickup)
    d_clean = clean_place_query(delivery)
    l_clean = clean_place_query(location)

    for key, val in (("pickup", p_clean), ("delivery", d_clean)):
        if not val:
            continue
        norm = val.lower()
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        anchors.append({"key": key, "label": _ANCHOR_LABELS[key], "query": val})

    if not anchors and l_clean:
        anchors.append({"key": "location", "label": _ANCHOR_LABELS["location"], "query": l_clean})
    elif l_clean and len(anchors) == 1 and l_clean.lower() not in seen_norm:
        anchors.append({"key": "location", "label": _ANCHOR_LABELS["location"], "query": l_clean})

    return anchors


def _serper_search_places(
    anchors: list[dict[str, Any]],
    specialty: str,
    api_key: str,
    http_fetch: Callable[..., Any],
) -> tuple[list[dict[str, Any]], int]:
    """Query Google Places via Serper for each route anchor."""
    def query_anchor(anchor: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
        q = f"{specialty} near {anchor['query']}"
        body = json.dumps({"q": q, "gl": "us", "hl": "en"}).encode("utf-8")
        try:
            res = http_fetch(
                "https://google.serper.dev/places",
                method="POST",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                data=body,
                timeout=6.5,
            )
            places = res.get("places", []) if isinstance(res, dict) else []
            return anchor, places if isinstance(places, list) else [], True
        except Exception:
            return anchor, [], False

    with ThreadPoolExecutor(max_workers=min(3, len(anchors))) as pool:
        responses = list(pool.map(query_anchor, anchors))

    ok_calls = sum(1 for _, _, ok in responses if ok)
    shops: list[dict[str, Any]] = []
    for anchor, places, ok in responses:
        if not ok:
            continue
        for idx, place in enumerate(places[:8]):
            if not isinstance(place, dict):
                continue
            name = str(place.get("title") or "").strip()
            if not name:
                continue
            address = str(place.get("address") or "").strip()
            phone = str(place.get("phoneNumber") or "").strip()
            website = str(place.get("website") or "").strip()
            lat = place.get("latitude")
            lon = place.get("longitude")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                anchor.setdefault("lat", float(lat))
                anchor.setdefault("lon", float(lon))
            cid = str(place.get("cid") or "").strip()
            if cid:
                map_url = f"https://maps.google.com/?cid={quote(cid)}"
            else:
                map_url = f"https://www.google.com/maps/search/?api=1&query={quote(f'{name} {address}'.strip())}"
            rating = place.get("rating")
            rating_count = place.get("ratingCount")
            category = str(place.get("category") or place.get("type") or "Commercial Repair").strip()
            heavy_match = bool(_HEAVY_NAME_RE.search(f"{name} {category}"))
            if _LIGHT_AUTO_BRAND_RE.search(name) and not heavy_match:
                continue
            rank_score = (35 if heavy_match else 15) + max(0, 12 - idx * 2)
            if isinstance(rating, (int, float)) and rating >= 4.0:
                rank_score += 5
            if phone:
                rank_score += 3
            shops.append(
                {
                    "shop": name[:120],
                    "address": address[:180],
                    "phone": phone[:40],
                    "website": website[:220] if website.startswith(("http://", "https://")) else "",
                    "opening_hours": "",
                    "rating": round(float(rating), 1) if isinstance(rating, (int, float)) else None,
                    "rating_count": int(rating_count) if isinstance(rating_count, (int, float)) else 0,
                    "category": category[:60],
                    "anchor_key": anchor["key"],
                    "anchor_label": anchor["label"],
                    "anchor_query": anchor["query"],
                    "distance_miles": None,
                    "lat": float(lat) if isinstance(lat, (int, float)) else None,
                    "lon": float(lon) if isinstance(lon, (int, float)) else None,
                    "map_url": map_url,
                    "provider": "Google Places",
                    "source_type": "live_route",
                    "live": True,
                    "_rank": rank_score,
                }
            )
    return shops, ok_calls


def _nominatim_geocode(query: str, http_fetch: Callable[..., Any]) -> tuple[float, float, str] | None:
    """Resolve a place string to (lat, lon, display_name) via OpenStreetMap Nominatim."""
    candidates = [query]
    fallback = _city_state_fallback(query)
    if fallback and fallback.lower() != query.lower():
        candidates.append(fallback)
    for q in candidates:
        params = urlencode({"q": q, "format": "jsonv2", "limit": "1", "countrycodes": "us,ca"})
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        try:
            data = http_fetch(url, method="GET", timeout=5.0)
        except Exception:
            return None
        if isinstance(data, list) and data:
            top = data[0]
            try:
                lat = float(top["lat"])
                lon = float(top["lon"])
                label = str(top.get("display_name") or q).split(",")[0:2]
                return lat, lon, ", ".join(s.strip() for s in label if s.strip()) or q
            except (KeyError, TypeError, ValueError):
                continue
    return None


def _osm_format_address(tags: dict[str, Any]) -> str:
    """Format an OSM element's tags into a single readable address line."""
    house = str(tags.get("addr:housenumber") or "").strip()
    street = str(tags.get("addr:street") or "").strip()
    city = str(tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village") or "").strip()
    state = str(tags.get("addr:state") or "").strip()
    postcode = str(tags.get("addr:postcode") or "").strip()
    line1 = " ".join(p for p in (house, street) if p)
    line2 = ", ".join(p for p in (city, " ".join(p for p in (state, postcode) if p)) if p)
    if line1 and line2:
        return f"{line1}, {line2}"
    return line1 or line2 or str(tags.get("addr:full") or "").strip()


def _osm_category(tags: dict[str, Any], name: str, vehicle_type: str, systems: set[str]) -> tuple[str, int]:
    """Classify an OSM repair POI and compute relevance boost for commercial units."""
    shop_tag = str(tags.get("shop") or "").lower()
    amenity_tag = str(tags.get("amenity") or "").lower()
    craft_tag = str(tags.get("craft") or "").lower()
    hgv_tag = str(tags.get("hgv") or tags.get("truck") or "").lower()
    desc = " ".join(str(tags.get(k) or "") for k in ("name", "brand", "operator", "description", "service:vehicle", "tyres", "tires"))
    heavy = shop_tag in {"truck_repair", "trailer_repair"} or amenity_tag == "truck_repair" or hgv_tag in {"yes", "only", "designated"} or bool(_HEAVY_NAME_RE.search(desc))
    is_reefer = bool(re.search(r"reefer|thermo\s*king|carrier|refriger", desc, re.I))
    is_tire = shop_tag in {"tyres", "tires"} or bool(re.search(r"tire|tyre|speedco|bandag|pomp", desc, re.I))
    is_tow = craft_tag == "towing" or bool(re.search(r"tow(?:ing)?|wrecker|recovery", desc, re.I))

    score = 0
    if is_reefer and ("reefer" in systems or vehicle_type == "reefer"):
        score += 48
        cat = "Reefer / Refrigeration"
    elif is_tire and ("tires" in systems or vehicle_type == "trailer"):
        score += 44 if heavy else 26
        cat = "Commercial Tire Service" if heavy else "Tire Service"
    elif is_tow and "towing" in systems:
        score += 45
        cat = "Heavy Towing / Recovery"
    elif heavy:
        score += 40
        cat = "Heavy Truck / Diesel" if "trailer" not in name.lower() else "Trailer / Truck Repair"
    elif is_tire:
        score += 20
        cat = "Tire Service"
    elif is_tow:
        score += 18
        cat = "Towing Service"
    else:
        score += 6
        cat = "Repair / Service Shop"
    return cat, score


def _osm_search_places(
    anchors: list[dict[str, Any]],
    vehicle_type: str,
    systems: set[str],
    http_fetch: Callable[..., Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Geocode route anchors with Nominatim and query live POIs via Overpass API."""
    with ThreadPoolExecutor(max_workers=min(3, len(anchors))) as pool:
        coords = list(pool.map(lambda a: _nominatim_geocode(a["query"], http_fetch), anchors))

    geocoded_anchors: list[dict[str, Any]] = []
    for anchor, pt in zip(anchors, coords):
        if not pt:
            continue
        anchor["lat"], anchor["lon"], resolved = pt
        anchor["resolved_label"] = resolved
        geocoded_anchors.append(anchor)

    if not geocoded_anchors:
        return [], False

    # Insert real corridor midpoint anchor if pickup and delivery are separated (> 80 mi).
    p_anchor = next((a for a in geocoded_anchors if a["key"] == "pickup"), None)
    d_anchor = next((a for a in geocoded_anchors if a["key"] == "delivery"), None)
    if p_anchor and d_anchor:
        route_miles = _haversine_miles(p_anchor["lat"], p_anchor["lon"], d_anchor["lat"], d_anchor["lon"])
        if 80.0 < route_miles < 2800.0:
            geocoded_anchors.append(
                {
                    "key": "corridor",
                    "label": _ANCHOR_LABELS["corridor"],
                    "query": f"Midpoint ({int(round(route_miles))} mi route)",
                    "lat": round((p_anchor["lat"] + d_anchor["lat"]) / 2.0, 5),
                    "lon": round((p_anchor["lon"] + d_anchor["lon"]) / 2.0, 5),
                    "resolved_label": "Route Midpoint",
                }
            )

    parts: list[str] = []
    for a in geocoded_anchors:
        lat, lon = a["lat"], a["lon"]
        parts.extend(
            [
                f'nwr["shop"~"truck_repair|trailer_repair|tyres|tires|car_repair"](around:35000,{lat},{lon});',
                f'nwr["amenity"="truck_repair"](around:40000,{lat},{lon});',
                f'nwr["craft"="towing"](around:35000,{lat},{lon});',
            ]
        )
    overpass_ql = f"[out:json][timeout:7];({''.join(parts)});out center tags 80;"
    body = urlencode({"data": overpass_ql}).encode("utf-8")

    raw_overpass: Any = None
    overpass_ok = False
    for endpoint in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"):
        try:
            raw_overpass = http_fetch(
                endpoint,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=body,
                timeout=8.0,
            )
            if isinstance(raw_overpass, dict) and isinstance(raw_overpass.get("elements"), list):
                overpass_ok = True
                break
        except Exception:
            continue

    if not overpass_ok:
        return [], False

    shops: list[dict[str, Any]] = []
    for el in raw_overpass.get("elements", []):
        if not isinstance(el, dict):
            continue
        tags = el.get("tags") or {}
        if not isinstance(tags, dict):
            continue
        name = str(tags.get("name") or tags.get("brand") or tags.get("operator") or "").strip()
        if not name:
            continue
        center = el.get("center") if isinstance(el.get("center"), dict) else {}
        lat = el.get("lat") if el.get("lat") is not None else center.get("lat")
        lon = el.get("lon") if el.get("lon") is not None else center.get("lon")
        try:
            f_lat = float(lat)
            f_lon = float(lon)
        except (TypeError, ValueError):
            continue

        nearest = min(geocoded_anchors, key=lambda a: _haversine_miles(f_lat, f_lon, a["lat"], a["lon"]))
        dist_mi = round(_haversine_miles(f_lat, f_lon, nearest["lat"], nearest["lon"]), 1)
        if dist_mi > 28.0:
            continue

        category, cat_score = _osm_category(tags, name, vehicle_type, systems)
        if _LIGHT_AUTO_BRAND_RE.search(name) and cat_score < 20:
            continue

        phone = str(tags.get("phone") or tags.get("contact:phone") or "").strip()
        website = str(tags.get("website") or tags.get("contact:website") or "").strip()
        hours = str(tags.get("opening_hours") or "").strip()
        address = _osm_format_address(tags)
        proximity_bonus = max(0.0, 22.0 - dist_mi)
        rank_score = cat_score + proximity_bonus + (3 if phone else 0) + (2 if address else 0)
        map_url = f"https://www.google.com/maps/search/?api=1&query={quote(f'{name} {address}'.strip() or f'{f_lat:.5f},{f_lon:.5f}')}"

        shops.append(
            {
                "shop": name[:120],
                "address": address[:180],
                "phone": phone[:40],
                "website": website[:220] if website.startswith(("http://", "https://")) else "",
                "opening_hours": hours[:100],
                "rating": None,
                "rating_count": 0,
                "category": category[:60],
                "anchor_key": nearest["key"],
                "anchor_label": nearest["label"],
                "anchor_query": nearest.get("resolved_label") or nearest["query"],
                "distance_miles": dist_mi,
                "lat": f_lat,
                "lon": f_lon,
                "map_url": map_url,
                "provider": "OpenStreetMap",
                "source_type": "live_route",
                "live": True,
                "_rank": rank_score,
            }
        )

    return shops, True


def _dedupe_and_rank(shops: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Deduplicate shops by normalized name + anchor/coords and sort best first."""
    ordered = sorted(shops, key=lambda s: (-float(s.get("_rank") or 0), float(s.get("distance_miles") or 999)))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in ordered:
        name_key = re.sub(r"[^a-z0-9]+", "", str(s.get("shop") or "").lower())
        addr_key = re.sub(r"[^a-z0-9]+", "", str(s.get("address") or "").lower())[:24]
        sig = f"{name_key}:{addr_key or s.get('anchor_key', '')}"
        if not name_key or sig in seen:
            continue
        seen.add(sig)
        clean = {k: v for k, v in s.items() if k != "_rank"}
        deduped.append(clean)
        if len(deduped) >= limit:
            break
    return deduped


def search_live_repair_shops(
    pickup: str = "",
    delivery: str = "",
    location: str = "",
    vehicle_type: str = "",
    systems: set[str] | list[str] | tuple[str, ...] = (),
    *,
    force: bool = False,
    limit: int = 8,
    http_fetch: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Search real-time truck/trailer/reefer repair shops near pickup and delivery."""
    norm_systems = sorted({str(s).strip().lower() for s in (systems or ()) if str(s).strip()})
    anchors = _build_anchors(pickup, delivery, location)
    if not anchors:
        return {
            "status": "no_route",
            "provider": "",
            "anchors": [],
            "shops": [],
            "note": "Add a pickup, delivery, or location on this case to search live repair shops along the route.",
        }

    cache_key = (
        anchors[0]["query"].lower(),
        anchors[1]["query"].lower() if len(anchors) > 1 else "",
        clean_place_query(location).lower(),
        str(vehicle_type or "").strip().lower(),
        ",".join(norm_systems),
        str(int(limit)),
    )
    now = time.time()
    use_cache = not force and http_fetch is None
    if use_cache:
        with _CACHE_LOCK:
            cached = _RESULT_CACHE.get(cache_key)
            if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
                return json.loads(json.dumps(cached[1]))

    fetcher = http_fetch or _default_http_fetch
    sys_set = set(norm_systems)
    specialty = _specialty_phrase(vehicle_type, sys_set)
    serper_key = os.getenv("SERPER_API_KEY", "").strip()

    shops: list[dict[str, Any]] = []
    provider = ""
    any_ok = False

    if serper_key:
        serper_shops, ok_calls = _serper_search_places(anchors, specialty, serper_key, fetcher)
        if ok_calls > 0:
            any_ok = True
            provider = "Google Places"
        if serper_shops:
            shops = serper_shops

    if not shops:
        osm_shops, osm_ok = _osm_search_places(anchors, str(vehicle_type or "").lower(), sys_set, fetcher)
        if osm_ok:
            any_ok = True
            if osm_shops or not provider:
                provider = "OpenStreetMap"
            shops = osm_shops

    final_shops = _dedupe_and_rank(shops, limit=max(1, min(12, int(limit or 8))))
    anchor_meta = [
        {
            "key": a["key"],
            "label": a["label"],
            "query": a.get("resolved_label") or a["query"],
            "lat": a.get("lat"),
            "lon": a.get("lon"),
        }
        for a in anchors
    ]

    if final_shops:
        status = "ok"
        note = f"Live shops loaded via {provider} near " + ", ".join(a["query"] for a in anchors[:2]) + "."
    elif any_ok:
        status = "no_results"
        note = "Live map search returned no matching commercial repair shops right near these stops."
    else:
        status = "unavailable"
        note = "Live repair shop search could not reach external map services right now."

    result = {
        "status": status,
        "provider": provider,
        "anchors": anchor_meta,
        "shops": final_shops,
        "note": note,
        "searched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }

    if use_cache and status in {"ok", "no_results"}:
        with _CACHE_LOCK:
            if len(_RESULT_CACHE) >= _CACHE_MAX_ENTRIES:
                oldest_key = min(_RESULT_CACHE.items(), key=lambda item: item[1][0])[0]
                _RESULT_CACHE.pop(oldest_key, None)
            _RESULT_CACHE[cache_key] = (now, json.loads(json.dumps(result)))

    return result


__all__ = ["clean_place_query", "search_live_repair_shops"]
