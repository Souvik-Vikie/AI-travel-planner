import os

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from travel_agent import llm, tools
from travel_agent.config import settings
from travel_agent.runner import run_trip

REQ = {"origin_city": "Delhi", "region": "india", "month": 12, "days": 5, "travellers": 2,
       "budget": 80000, "interests": ["beach", "food"]}
approve = lambda plan: {"decision": "approve"}


def test_happy_path_books_and_writes_pdf():
    s = run_trip(None, approve, request=REQ, on_log=lambda _: None)
    assert s["status"] == "done"
    assert s["cost"]["total"] <= REQ["budget"]
    assert s["bookings"]["flight"]["status"] == "confirmed"
    assert os.path.exists(s["pdf_path"])


def test_interactive_intake_uses_interrupt():
    asked = []
    s = run_trip(lambda: asked.append(1) or REQ, approve, on_log=lambda _: None)
    assert asked == [1] and s["status"] == "done"


def test_nothing_is_booked_without_approval():
    s = run_trip(None, lambda plan: {"decision": "cancel"}, request=REQ, on_log=lambda _: None)
    assert s["status"] == "cancelled" and "bookings" not in s


def test_tight_budget_triggers_replanning():
    s = run_trip(None, approve, request={**REQ, "budget": 30000}, on_log=lambda _: None)
    assert s["metrics"]["replan_count"] >= 1
    assert any("Budget check FAILED" in line for line in s["log"])


def test_impossible_budget_ends_without_booking():
    s = run_trip(None, approve, request={**REQ, "budget": 5000, "days": 8, "travellers": 4}, on_log=lambda _: None)
    assert s["status"] == "no_feasible_plan" and "bookings" not in s


def test_sold_out_flight_recovers_with_next_offer(monkeypatch):
    calls = []
    real = tools.book_flight.func

    def flaky(offer, traveller=None):
        calls.append(offer["offer_id"])
        if len(calls) == 1:
            return {"status": "sold_out", "reason": "test", "mode": "simulated"}
        return real(offer, traveller)

    monkeypatch.setattr(tools.book_flight, "func", flaky)
    s = run_trip(None, approve, request=REQ, on_log=lambda _: None)
    assert s["status"] == "done" and len(set(calls)) == 2
    assert s["metrics"]["sold_out_count"] == 1


def test_rejection_feedback_replans():
    answers = iter([{"decision": "reject", "feedback": "somewhere else please"}, {"decision": "approve"}])
    seen = []
    s = run_trip(None, lambda plan: seen.append(plan["destination"]) or next(answers), request=REQ, on_log=lambda _: None)
    assert s["status"] == "done" and seen[0] != seen[1]


def test_llm_choice_is_used_when_valid_and_ignored_when_garbage(monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "hf_endpoint")
    fake = FakeListChatModel(responses=['{"choice": "goa", "reason": "Beach season and great food."}', "not json"] * 5)
    monkeypatch.setattr(llm, "get_chat_model", lambda: fake)
    s = run_trip(None, approve, request=REQ, on_log=lambda _: None)
    assert s["destination"]["id"] == "goa" and s["status"] == "done"
    assert len(s["itinerary"]) == REQ["days"]  # garbage itinerary reply -> template fallback


def test_provider_rejection_is_not_treated_as_sold_out(monkeypatch):
    """A rejected booking request must stop cleanly, not burn through offers/destinations."""
    monkeypatch.setattr(tools.book_flight, "func",
                        lambda offer, traveller=None: {"status": "error", "reason": "bad data", "mode": "duffel test mode"})
    s = run_trip(None, approve, request=REQ, on_log=lambda _: None)
    assert s["status"] == "booking_failed" and "bookings" not in s
    assert s["metrics"].get("sold_out_count", 0) == 0


def test_interests_drive_destination_choice():
    beach = run_trip(None, approve, request={**REQ, "region": "abroad", "budget": 200000, "interests": ["beach", "nightlife"]}, on_log=lambda _: None)
    mountains = run_trip(None, approve, request={**REQ, "region": "abroad", "budget": 200000, "interests": ["mountains", "spiritual"]}, on_log=lambda _: None)
    assert beach["destination"]["id"] != mountains["destination"]["id"]


def test_user_can_switch_destination_from_candidates():
    seen = []

    def decide(plan):
        seen.append(plan["destination"])
        return {"decision": "switch", "destination_id": "jaipur"} if len(seen) == 1 else {"decision": "approve"}

    s = run_trip(None, decide, request=REQ, on_log=lambda _: None)
    assert s["destination"]["id"] == "jaipur" and s["status"] == "done" and len(seen) == 2


def test_user_chosen_destination_is_not_swapped_even_if_over_budget():
    from travel_agent import nodes

    state = {"cost": {"total": 100000}, "request": {"budget": 50000}, "destination": {"id": "goa"}}
    assert nodes.budget_critic(state)["next_action"] == "replan"                    # agent's own pick: re-plan
    assert nodes.budget_critic({**state, "user_chose": "goa"})["next_action"] == "itinerary"  # human's pick: show it


