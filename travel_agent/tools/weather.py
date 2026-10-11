"""Seasonality tool: Open-Meteo historical archive (free, no key) + built-in season knowledge."""
import calendar
import datetime as dt

import requests
from langchain_core.tools import tool

from ..config import settings
from ..data import get_destination
from .common import cache_get, cache_put

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_network_ok = True  # circuit breaker: stop calling after the first network failure


def _fetch_climate(lat: float, lon: float, month: int) -> dict | None:
    """Average weather for `month` using last year's observations."""
    global _network_ok
    year = dt.date.today().year - 1
    key = {"lat": lat, "lon": lon, "month": month, "year": year}
    if (cached := cache_get("weather", key)) is not None:
        return cached
    if not (settings.use_open_meteo and _network_ok):
        return None
    last = calendar.monthrange(year, month)[1]
    try:
        r = requests.get(ARCHIVE_URL, timeout=6, params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "start_date": f"{year}-{month:02d}-01", "end_date": f"{year}-{month:02d}-{last}",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        })
        r.raise_for_status()
        daily = r.json()["daily"]
    except Exception:
        _network_ok = False
        return None
    tmax = [v for v in daily["temperature_2m_max"] if v is not None]
    tmin = [v for v in daily["temperature_2m_min"] if v is not None]
    rain = [v for v in daily["precipitation_sum"] if v is not None]
    climate = {
        "avg_max_c": round(sum(tmax) / len(tmax), 1),
        "avg_min_c": round(sum(tmin) / len(tmin), 1),
        "rainy_day_ratio": round(sum(1 for v in rain if v > 2.0) / max(1, len(rain)), 2),
        "source": f"Open-Meteo archive {year}",
    }
    cache_put("weather", key, climate)
    return climate


def season_score(dest: dict, month: int) -> tuple[float, dict]:
    """0..1 score for how good `month` is for `dest`, with an explanation payload."""
    if month in dest["best"]:
        knowledge = 0.95
    elif month in dest["avoid"]:
        knowledge = 0.2
    else:
        knowledge = 0.6
    climate = _fetch_climate(dest["lat"], dest["lon"], month)
    if not climate:
        return knowledge, {"source": "season knowledge base", "label": _label(knowledge)}
    # Comfort: ideal daytime max 18-31C, rain lowers the score.
    t = climate["avg_max_c"]
    comfort = 1.0 if 18 <= t <= 31 else max(0.0, 1 - (min(abs(t - 18), abs(t - 31)) / 12))
    weather = comfort * (1 - 0.7 * climate["rainy_day_ratio"])
    # Snow/mountain trips are *meant* to be cold, so trust the knowledge base more there.
    w = 0.3 if "snow" in dest["tags"] else 0.6
    score = w * weather + (1 - w) * knowledge
    return round(score, 3), {**climate, "label": _label(score)}


def _label(score: float) -> str:
    return "great season" if score >= 0.75 else "okay season" if score >= 0.5 else "off season"


@tool
def check_season(destination_id: str, month: int) -> dict:
    """Return a 0-1 seasonality score and climate summary for visiting a destination in a given month."""
    score, info = season_score(get_destination(destination_id), month)
    return {"destination_id": destination_id, "month": month, "score": score, **info}
