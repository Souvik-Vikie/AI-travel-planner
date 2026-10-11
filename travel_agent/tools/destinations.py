"""Destination research tool: scores every candidate on season, cost, travel time and interests."""
import math

from langchain_core.tools import tool

from ..data import DESTINATIONS
from .common import haversine_km
from .flights import estimate_fare_inr, estimate_hours
from .weather import season_score

WEIGHTS = {"season": 0.25, "cost": 0.20, "travel_time": 0.10, "interests": 0.45}


def estimate_trip_cost(dest: dict, km: float, days: int, travellers: int) -> float:
    nights = max(1, days - 1)
    rooms = math.ceil(travellers / 2)
    flights = 2 * estimate_fare_inr(km, dest["region"] == "abroad") * travellers
    return flights + dest["hotel"] * nights * rooms + dest["daily"] * days * travellers


@tool
def rank_destinations(origin: dict, region: str, month: int, budget: float, days: int,
                      travellers: int, interests: list[str]) -> list[dict]:
    """Rank destinations for a trip. region is 'india', 'abroad' or 'any'.
    Returns candidates sorted by a weighted score with the breakdown for explainability."""
    ranked = []
    for d in DESTINATIONS:
        if region != "any" and d["region"] != region:
            continue
        if d["iata"] == origin["iata"]:
            continue
        km = haversine_km(origin["lat"], origin["lon"], d["lat"], d["lon"])
        est = estimate_trip_cost(d, km, days, travellers)
        hours = estimate_hours(km)
        s_season, climate = season_score(d, month)
        # cost fit: 1.0 up to 80% of budget, falling to 0 at 120% of budget
        s_cost = max(0.0, min(1.0, (1.2 * budget - est) / (0.4 * budget)))
        s_time = max(0.0, 1 - hours / 14)
        matched = sorted(set(d["tags"]) & set(interests))
        # Reward covering what the traveller asked for; reaching 2 matches already counts as a strong fit.
        s_int = min(1.0, len(matched) / min(len(interests), 2)) if interests else 0.5
        score = (WEIGHTS["season"] * s_season + WEIGHTS["cost"] * s_cost
                 + WEIGHTS["travel_time"] * s_time + WEIGHTS["interests"] * s_int)
        ranked.append({
            "id": d["id"], "name": d["name"], "country": d["country"], "score": round(score, 3),
            "estimated_cost": round(est, -2), "flight_hours": round(hours, 1),
            "breakdown": {"season": round(s_season, 2), "cost": round(s_cost, 2),
                          "travel_time": round(s_time, 2), "interests": round(s_int, 2)},
            "climate": climate, "matched_interests": matched,
        })
    ranked.sort(key=lambda c: c["score"], reverse=True)
    affordable = [c for c in ranked if c["estimated_cost"] <= 1.25 * budget]
    # Keep at least 3 options even on tiny budgets (cheapest first) so the agent can still try.
    if len(affordable) < 3:
        affordable += sorted([c for c in ranked if c not in affordable], key=lambda c: c["estimated_cost"])[:3 - len(affordable)]
    return affordable[:8]
