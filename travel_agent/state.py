"""Graph state shared by every node."""
import operator
from typing import Annotated, TypedDict


class TripRequest(TypedDict, total=False):
    origin_city: str
    origin: dict          # {iata, lat, lon}
    region: str           # india | abroad | any
    month: int
    year: int
    start_date: str       # YYYY-MM-DD
    days: int
    travellers: int
    budget: float         # INR, total for the whole group
    interests: list[str]


class TravelState(TypedDict, total=False):
    request: TripRequest
    candidates: list[dict]
    excluded: list[str]
    override: str                # destination id the user picked from the table (one-shot)
    user_chose: str              # destination the user picked; budget critic won't override it
    destination: dict
    selection_reason: str
    flight_options: list[dict]   # offers on the chosen dates, cheapest first
    flight: dict
    flight_meta: dict            # first-found vs chosen, flex-date savings
    hotel_options: list[dict]    # cheapest first
    hotel: dict
    hotel_meta: dict
    cost: dict
    cheapest_seen: dict          # cheapest plan priced so far (for 'no feasible plan' advice)
    budget_ok: bool
    replan_count: int
    next_action: str
    feedback: str
    itinerary: list[dict]
    approved: dict               # {destination, hotel_id, total} the human signed off on
    bookings: dict
    pdf_path: str
    status: str
    log: Annotated[list[str], operator.add]   # agent's reasoning trace (append-only)
    metrics: dict
