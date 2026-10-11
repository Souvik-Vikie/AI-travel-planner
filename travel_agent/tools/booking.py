"""Booking tools. Flights: real Duffel *test-mode* order when available, else simulated.
Hotels: always simulated (real hotel booking APIs require partner access)."""
import uuid

import requests
from langchain_core.tools import tool

from ..config import settings
from .common import stable_rng
from .flights import DUFFEL_URL


class SoldOut(Exception):
    """The offer is gone (expired / sold out / price changed): trying another offer makes sense."""


class BookingError(Exception):
    """The request itself was rejected (bad data, auth...): another offer will not help."""


# Duffel error codes that mean "this offer is no longer bookable".
_GONE_CODES = {"offer_no_longer_available", "offer_expired", "price_changed", "fare_unavailable"}


def _duffel_order(offer: dict, traveller: dict) -> str:
    passengers = [{
        "id": pid, "title": "mr", "gender": "m", "born_on": "1995-01-01",
        "given_name": traveller.get("given_name", "Test"),
        # Duffel only accepts letters in names, so tell passengers apart with A, B, C...
        "family_name": f"{traveller.get('family_name', 'Traveller')}{chr(65 + i % 26)}",
        "email": "traveller@example.com", "phone_number": "+919000000000",
    } for i, pid in enumerate(offer["passenger_ids"])]
    r = requests.post(f"{DUFFEL_URL}/air/orders", timeout=40,
                      headers={"Authorization": f"Bearer {settings.duffel_token}", "Duffel-Version": "v2",
                               "Content-Type": "application/json", "Accept": "application/json"},
                      json={"data": {"type": "instant", "selected_offers": [offer["offer_id"]],
                                     "passengers": passengers,
                                     "payments": [{"type": "balance", "currency": offer["currency"],
                                                   "amount": offer["amount"]}]}})
    if r.status_code >= 400:
        err = (r.json().get("errors") or [{}])[0]
        message = err.get("message", f"HTTP {r.status_code}")
        raise (SoldOut if err.get("code") in _GONE_CODES else BookingError)(message)
    return r.json()["data"]["booking_reference"]


@tool
def book_flight(offer: dict, traveller: dict | None = None) -> dict:
    """Book a flight offer. Returns {status: 'confirmed'|'sold_out'|'error', reference, mode}."""
    traveller = traveller or {}
    if offer.get("provider") == "duffel":
        try:
            return {"status": "confirmed", "reference": _duffel_order(offer, traveller), "mode": "duffel test mode"}
        except SoldOut as exc:
            return {"status": "sold_out", "reason": str(exc), "mode": "duffel test mode"}
        except (BookingError, requests.RequestException) as exc:
            return {"status": "error", "reason": str(exc), "mode": "duffel test mode"}
    # Simulated booking: a deterministic fraction of offers is "sold out" to exercise re-planning.
    if stable_rng("soldout", offer["offer_id"]).random() < settings.mock_sold_out_rate:
        return {"status": "sold_out", "reason": "Fare class no longer available", "mode": "simulated"}
    return {"status": "confirmed", "reference": "PNR" + uuid.uuid4().hex[:6].upper(), "mode": "simulated"}


@tool
def book_hotel(hotel: dict, check_in: str, check_out: str, rooms: int) -> dict:
    """Reserve a hotel (simulated). Returns a confirmation number."""
    return {"status": "confirmed", "reference": "HTL-" + uuid.uuid4().hex[:8].upper(), "mode": "simulated",
            "hotel": hotel["name"], "check_in": check_in, "check_out": check_out, "rooms": rooms}
