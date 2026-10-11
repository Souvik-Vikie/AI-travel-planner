"""Flight search tool. Providers: Duffel (test mode) -> SerpAPI Google Flights -> offline mock."""
import re

import requests
from langchain_core.tools import tool

from ..config import settings, to_inr
from .common import cache_get, cache_put, haversine_km, stable_rng

DUFFEL_URL = "https://api.duffel.com"
AIRLINES = ["IndiGo", "Air India", "Akasa Air", "SpiceJet", "Vistara Express", "Emirates", "Thai Airways",
            "Singapore Airlines", "AirAsia", "Qatar Airways"]


def estimate_fare_inr(km: float, international: bool) -> float:
    """Typical one-way economy fare per person (used for ranking and by the mock)."""
    return (4000 + 3.5 * km) if international else (1800 + 2.6 * km)


def estimate_hours(km: float) -> float:
    return km / 750 + 0.6 + (2.5 if km > 3500 else 0)


# ---------------------------------------------------------------- providers
def _mock_search(o: dict, d: dict, depart: str, ret: str, adults: int, peak: bool) -> list[dict]:
    km = haversine_km(o["lat"], o["lon"], d["lat"], d["lon"])
    base = 2 * estimate_fare_inr(km, d["international"]) * (1.15 if peak else 1.0)
    rng = stable_rng("flight", o["iata"], d["iata"], depart, ret)
    day_factor = rng.uniform(0.8, 1.25)  # some dates are simply cheaper
    pool = [a for a in AIRLINES[:5]] if not d["international"] else AIRLINES
    offers = []
    for i in range(rng.randint(3, 6)):
        stops = 0 if km < 2500 and rng.random() < 0.7 else rng.choice([0, 1, 1])
        per_person = base * day_factor * rng.uniform(0.85, 1.35) * (0.92 if stops else 1.0)
        offers.append({
            "offer_id": f"mock_{o['iata']}{d['iata']}_{depart}_{i}",
            "provider": "mock",
            "airline": rng.choice(pool),
            "stops": stops,
            "duration_hours": round(estimate_hours(km) + 2.0 * stops, 1),
            "depart_date": depart, "return_date": ret,
            "price_inr": round(per_person * adults, -1),
        })
    return offers  # deliberately NOT sorted: "first found" != cheapest


def _duffel_search(o, d, depart, ret, adults) -> list[dict]:
    key = {"o": o["iata"], "d": d["iata"], "dep": depart, "ret": ret, "n": adults}
    if (cached := cache_get("duffel_offers", key, ttl=600)) is not None:  # 10 min: offers expire
        return cached
    r = requests.post(f"{DUFFEL_URL}/air/offer_requests", params={"return_offers": "true"}, timeout=40,
                      headers={"Authorization": f"Bearer {settings.duffel_token}", "Duffel-Version": "v2",
                               "Accept": "application/json", "Content-Type": "application/json"},
                      json={"data": {
                          "slices": [{"origin": o["iata"], "destination": d["iata"], "departure_date": depart},
                                     {"origin": d["iata"], "destination": o["iata"], "departure_date": ret}],
                          "passengers": [{"type": "adult"} for _ in range(adults)],
                          "cabin_class": "economy"}})
    r.raise_for_status()
    data = r.json()["data"]
    pax_ids = [p["id"] for p in data["passengers"]]
    offers = []
    for off in data["offers"][:15]:
        segs = off["slices"][0]["segments"]
        offers.append({
            "offer_id": off["id"], "provider": "duffel", "airline": off["owner"]["name"],
            "stops": len(segs) - 1, "duration_hours": _iso_hours(off["slices"][0].get("duration")),
            "depart_date": depart, "return_date": ret,
            "price_inr": round(to_inr(off["total_amount"], off["total_currency"]), -1),
            "amount": off["total_amount"], "currency": off["total_currency"], "passenger_ids": pax_ids,
        })
    cache_put("duffel_offers", key, offers)
    return offers


def _serpapi_search(o, d, depart, ret, adults) -> list[dict]:
    key = {"o": o["iata"], "d": d["iata"], "dep": depart, "ret": ret, "n": adults}
    data = cache_get("serp_flights", key)  # protect the 100 searches/month free quota
    if data is None:
        r = requests.get("https://serpapi.com/search.json", timeout=40, params={
            "engine": "google_flights", "departure_id": o["iata"], "arrival_id": d["iata"],
            "outbound_date": depart, "return_date": ret, "type": 1, "adults": adults,
            "currency": "INR", "hl": "en", "api_key": settings.serpapi_key})
        r.raise_for_status()
        data = r.json()
        cache_put("serp_flights", key, data)
    offers = []
    for i, f in enumerate(data.get("best_flights", []) + data.get("other_flights", [])):
        if "price" not in f:
            continue
        offers.append({
            "offer_id": f"serp_{o['iata']}{d['iata']}_{depart}_{i}", "provider": "serpapi",
            "airline": f["flights"][0].get("airline", "Unknown"), "stops": len(f["flights"]) - 1,
            "duration_hours": round(f.get("total_duration", 0) / 60, 1),
            "depart_date": depart, "return_date": ret, "price_inr": float(f["price"]),
        })
    return offers


def _iso_hours(iso: str | None) -> float:
    if not iso:
        return 0.0
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", iso)
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return round(d * 24 + h + mi / 60, 1)


@tool
def search_flights(origin: dict, destination: dict, depart_date: str, return_date: str,
                   adults: int, peak_season: bool = False) -> list[dict]:
    """Search round-trip economy flights. `origin`/`destination` are dicts with iata, lat, lon,
    international. Returns offers with total price (all passengers) in INR, in provider order."""
    provider = settings.flight_provider
    try:
        if provider == "duffel":
            offers = _duffel_search(origin, destination, depart_date, return_date, adults)
        elif provider == "serpapi":
            offers = _serpapi_search(origin, destination, depart_date, return_date, adults)
        else:
            offers = []
        if offers:
            return offers
    except Exception as exc:  # network / quota / unsupported route -> fall back, don't crash the agent
        print(f"  [flights] {provider} failed ({type(exc).__name__}); using mock data")
    return _mock_search(origin, destination, depart_date, return_date, adults, peak_season)
