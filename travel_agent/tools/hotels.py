"""Hotel search tool: SerpAPI Google Hotels (free tier) or offline mock."""
import requests
from langchain_core.tools import tool

from ..config import settings
from .common import cache_get, cache_put, stable_rng

PREFIX = ["Grand", "Royal", "Blue", "Green", "Sunset", "Heritage", "Urban", "Lotus", "Palm", "Silver", "Zen", "Crown"]
SUFFIX = ["Residency", "Inn", "Suites", "Retreat", "Boutique Hotel", "Resort", "Hostel & Rooms", "Palace", "Stay"]


def _mock(dest: dict, check_in: str, nights: int) -> list[dict]:
    rng = stable_rng("hotel", dest["id"], check_in, nights)
    hotels = []
    for i in range(8):
        factor = rng.uniform(0.45, 2.3)
        rating = min(4.9, max(3.0, 3.1 + factor * 0.75 + rng.uniform(-0.4, 0.4)))
        hotels.append({
            "hotel_id": f"mock_{dest['id']}_{i}", "provider": "mock",
            "name": f"{rng.choice(PREFIX)} {rng.choice(SUFFIX)} {dest['name'].split(' (')[0].split(' & ')[0]}",
            "rating": round(rating, 1), "price_per_night_inr": round(dest["hotel"] * factor, -1),
        })
    return hotels


def _serpapi(dest: dict, check_in: str, check_out: str, adults: int) -> list[dict]:
    key = {"q": dest["name"], "in": check_in, "out": check_out, "n": adults}
    data = cache_get("serp_hotels", key)
    if data is None:
        r = requests.get("https://serpapi.com/search.json", timeout=40, params={
            "engine": "google_hotels", "q": f"{dest['name']} {dest['country']} hotels",
            "check_in_date": check_in, "check_out_date": check_out, "adults": adults,
            "currency": "INR", "hl": "en", "gl": "in", "api_key": settings.serpapi_key})
        r.raise_for_status()
        data = r.json()
        cache_put("serp_hotels", key, data)
    hotels = []
    for i, p in enumerate(data.get("properties", [])):
        price = (p.get("rate_per_night") or {}).get("extracted_lowest")
        if not price:
            continue
        hotels.append({"hotel_id": f"serp_{dest['id']}_{i}", "provider": "serpapi", "name": p["name"],
                       "rating": float(p.get("overall_rating") or 3.5), "price_per_night_inr": float(price),
                       "link": p.get("link", "")})
    return hotels


@tool
def search_hotels(destination: dict, check_in: str, check_out: str, nights: int, adults: int) -> list[dict]:
    """Search hotels at a destination. Returns options (name, rating, price_per_night_inr) in provider order."""
    if settings.hotel_provider == "serpapi":
        try:
            if hotels := _serpapi(destination, check_in, check_out, adults):
                return hotels
        except Exception as exc:
            print(f"  [hotels] serpapi failed ({type(exc).__name__}); using mock data")
    return _mock(destination, check_in, nights)
