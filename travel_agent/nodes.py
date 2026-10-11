"""LangGraph nodes. Each node reads the state, calls tools / the LLM, and returns a partial update."""
import datetime as dt
import math

from langgraph.types import interrupt

from . import tools
from .config import settings
from .data import ALL_TAGS, get_destination, resolve_origin
from .llm import ask_json
from .pdf_report import build_pdf


def _metrics(state, **updates) -> dict:
    m = dict(state.get("metrics") or {})
    for k, v in updates.items():
        m[k] = m.get(k, 0) + v if isinstance(v, (int, float)) and k.endswith("_count") else v
    return m


def _dest_payload(dest: dict) -> dict:
    return {"iata": dest["iata"], "lat": dest["lat"], "lon": dest["lon"], "international": dest["region"] == "abroad",
            "id": dest["id"], "name": dest["name"], "country": dest["country"], "hotel": dest["hotel"]}


def _climate(state) -> dict:
    return next((c["climate"] for c in state["candidates"] if c["id"] == state["destination"]["id"]), {})


def _nights(req) -> int:
    return max(1, req["days"] - 1)


def _shift(date: str, days: int) -> str:
    return (dt.date.fromisoformat(date) + dt.timedelta(days=days)).isoformat()


# --------------------------------------------------------------------------- 1. intake (human input)
def normalize_request(answers: dict) -> dict:
    """Validate raw answers (from CLI / eval / API) into a TripRequest."""
    origin = resolve_origin(answers.get("origin_city", ""))
    if not origin:
        raise ValueError(f"Unknown origin city {answers.get('origin_city')!r}")
    month = int(answers["month"])
    today = dt.date.today()
    year = int(answers.get("year") or (today.year if month > today.month else today.year + 1))
    start = answers.get("start_date") or dt.date(year, month, int(answers.get("start_day", 10))).isoformat()
    region = str(answers.get("region", "any")).lower()
    region = {"domestic": "india", "international": "abroad", "both": "any"}.get(region, region)
    interests = answers.get("interests") or []
    if isinstance(interests, str):
        interests = [i.strip().lower() for i in interests.split(",") if i.strip()]
    return {
        "origin_city": answers["origin_city"], "origin": {"iata": origin[0], "lat": origin[1], "lon": origin[2]},
        "region": region if region in ("india", "abroad", "any") else "any",
        "month": month, "year": year, "start_date": start,
        "days": max(2, int(answers.get("days", 5))), "travellers": max(1, int(answers.get("travellers", 1))),
        "budget": float(answers["budget"]), "interests": [i for i in interests if i in ALL_TAGS],
    }


def intake(state):
    if state.get("request"):
        return {}
    answers = interrupt({"type": "intake", "message": "Tell me about your trip.", "interest_options": ALL_TAGS})
    req = normalize_request(answers)
    return {"request": req, "log": [f"Trip request: {req['travellers']} traveller(s), {req['days']} days in "
                                    f"{dt.date(2000, req['month'], 1):%B} from {req['origin']['iata']}, "
                                    f"budget INR {req['budget']:,.0f}, region={req['region']}"]}


# --------------------------------------------------------------------------- 2. research
def research(state):
    req = state["request"]
    cands = tools.rank_destinations.invoke({
        "origin": req["origin"], "region": req["region"], "month": req["month"], "budget": req["budget"],
        "days": req["days"], "travellers": req["travellers"], "interests": req["interests"]})
    top = ", ".join(f"{c['name']} ({c['score']:.2f})" for c in cands[:5])
    return {"candidates": cands, "excluded": state.get("excluded", []), "replan_count": 0,
            "metrics": _metrics(state, replan_count=0, sold_out_count=0),
            "log": [f"Researched {len(cands)} destinations on season/cost/travel-time/interests. Top: {top}"]}


# --------------------------------------------------------------------------- 3. pick destination (LLM)
def _why(c, req, best: bool) -> str:
    b = c["breakdown"]
    return (f"{c['name']} {'has the best overall score' if best else 'scores ' + format(c['score'], '.2f')}: "
            f"{c['climate'].get('label', '')} (season {b['season']}), "
            f"estimated INR {c['estimated_cost']:,.0f} against a budget of INR {req['budget']:,.0f}, "
            f"~{c['flight_hours']}h flight"
            + (f", matches your interests in {', '.join(c['matched_interests'])}" if c["matched_interests"] else "")
            + ".")


def pick_destination(state):
    req = state["request"]
    if override := state.get("override"):  # the human picked a row from the candidates table
        c = next(c for c in state["candidates"] if c["id"] == override)
        dest = get_destination(override)
        reason = f"You chose {c['name']} from the shortlist. " + _why(c, req, best=False)
        return {"destination": _dest_payload(dest), "selection_reason": reason, "status": "planning",
                "override": "", "user_chose": override, "next_action": "search",
                "log": [f"Switched to {dest['name']} as requested by the user."]}
    remaining = [c for c in state["candidates"] if c["id"] not in state.get("excluded", [])]
    if not remaining:
        best = state.get("cheapest_seen")
        hint = (f" Cheapest plan found was {best['destination']} at INR {best['total']:,.0f}; "
                f"raise the budget to at least that, or shorten the trip.") if best else ""
        return {"status": "no_feasible_plan", "next_action": "end",
                "log": ["No destination fits the budget after re-planning." + hint]}
    shortlist = remaining[:3]

    def fallback():
        return {"choice": shortlist[0]["id"], "reason": _why(shortlist[0], req, best=True)}

    def validate(obj):
        if obj.get("choice") not in {c["id"] for c in shortlist} or not obj.get("reason"):
            raise ValueError("invalid choice")
        return {"choice": obj["choice"], "reason": str(obj["reason"])[:600]}

    lines = [f"- id={c['id']}: {c['name']}, {c['country']}; score={c['score']}; est cost INR {c['estimated_cost']:,.0f}; "
             f"flight {c['flight_hours']}h; season={c['climate'].get('label')}; interests matched={c['matched_interests']}"
             for c in shortlist]
    decision, used_llm = ask_json(
        "You are a travel planner. Pick ONE destination from the shortlist, trading off season, cost, "
        "travel time and the traveller's interests. Output {\"choice\": <id>, \"reason\": <2 sentences>}.",
        f"Budget INR {req['budget']:,.0f} for {req['travellers']} people, {req['days']} days, month {req['month']}, "
        f"interests {req['interests']}.\nShortlist:\n" + "\n".join(lines),
        validate, fallback)
    dest = get_destination(decision["choice"])
    return {"destination": _dest_payload(dest), "selection_reason": decision["reason"], "status": "planning",
            "next_action": "search", "log": [f"Picked {dest['name']} ({'LLM' if used_llm else 'rules'}): {decision['reason']}"]}


# --------------------------------------------------------------------------- 4. flights (flexible dates)
def search_flights(state):
    req, dest = state["request"], state["destination"]
    peak = req["month"] in get_destination(dest["id"])["best"]
    by_offset = {}
    step = settings.flex_days if settings.flight_provider != "mock" else 1  # fewer real API calls
    for off in range(-settings.flex_days, settings.flex_days + 1, max(1, step)):
        dep = _shift(req["start_date"], off)
        if dt.date.fromisoformat(dep) <= dt.date.today():
            continue
        by_offset[off] = tools.search_flights.invoke({
            "origin": req["origin"], "destination": dest, "depart_date": dep,
            "return_date": _shift(dep, _nights(req)), "adults": req["travellers"], "peak_season": peak})
    if 0 not in by_offset:  # requested date in the past; treat the first valid date as "requested"
        by_offset[0] = by_offset.pop(min(by_offset))
    first_found = by_offset[0][0]["price_inr"]
    requested_best = min(o["price_inr"] for o in by_offset[0])
    best_off = min(by_offset, key=lambda k: min(o["price_inr"] for o in by_offset[k]))
    best_price = min(o["price_inr"] for o in by_offset[best_off])
    saving = (requested_best - best_price) / requested_best
    chosen_off = best_off if saving >= settings.flex_min_saving else 0
    options = sorted(by_offset[chosen_off], key=lambda o: o["price_inr"])
    if chosen_off:
        msg = (f"Flexible dates: shifting departure by {chosen_off:+d} day(s) saves {saving:.0%} "
               f"(INR {requested_best:,.0f} -> {best_price:,.0f}).")
    else:
        msg = f"Kept requested dates (best date shift would save only {max(saving, 0):.0%})."
    f = options[0]
    return {"flight_options": options, "flight": f,
            "flight_meta": {"first_found_price": first_found, "requested_date_best": requested_best,
                            "chosen_offset": chosen_off, "flex_saving": requested_best - f["price_inr"]},
            "log": [f"Searched {sum(map(len, by_offset.values()))} flight offers across {len(by_offset)} dates. {msg} "
                    f"Cheapest: {f['airline']} INR {f['price_inr']:,.0f} ({f['provider']})."]}


# --------------------------------------------------------------------------- 5. hotels
def search_hotels(state):
    req, dest, f = state["request"], state["destination"], state["flight"]
    nights = _nights(req)
    raw = tools.search_hotels.invoke({"destination": dest, "check_in": f["depart_date"], "check_out": f["return_date"],
                                      "nights": nights, "adults": req["travellers"]})
    options = sorted(raw, key=lambda h: h["price_per_night_inr"])
    # Best value: cheapest place rated 4.0+, else 3.5+, else the cheapest.
    pick = next((h for h in options if h["rating"] >= 4.0), None) or \
        next((h for h in options if h["rating"] >= 3.5), options[0])
    rooms = math.ceil(req["travellers"] / 2)
    return {"hotel_options": options, "hotel": pick,
            "hotel_meta": {"first_found_total": raw[0]["price_per_night_inr"] * nights * rooms},
            "log": [f"Found {len(options)} hotels; chose {pick['name']} ({pick['rating']}*, "
                    f"INR {pick['price_per_night_inr']:,.0f}/night) as the cheapest well-rated option."]}


# --------------------------------------------------------------------------- 6. price + budget critic
def price_trip(state):
    req, f, h = state["request"], state["flight"], state["hotel"]
    dest = get_destination(state["destination"]["id"])
    rooms, nights = math.ceil(req["travellers"] / 2), _nights(req)
    cost = {"flights": f["price_inr"], "hotel": h["price_per_night_inr"] * nights * rooms,
            "daily_expenses": dest["daily"] * req["days"] * req["travellers"], "rooms": rooms, "nights": nights}
    sub = cost["flights"] + cost["hotel"] + cost["daily_expenses"]
    cost["buffer"] = round(sub * settings.misc_buffer, -1)
    cost["total"] = round(sub + cost["buffer"], -1)
    best = state.get("cheapest_seen")
    if not best or cost["total"] < best["total"]:
        best = {"destination": dest["name"], "total": cost["total"]}
    return {"cost": cost, "cheapest_seen": best}


def budget_critic(state):
    total, budget = state["cost"]["total"], state["request"]["budget"]
    if total > budget and state.get("user_chose") == state["destination"]["id"]:
        # The human explicitly chose this place: don't silently swap it, show the overrun at the approval gate.
        return {"budget_ok": False, "next_action": "itinerary",
                "log": [f"You chose this destination and it is INR {total - budget:,.0f} over budget; "
                        "showing it for your decision."]}
    if total <= budget:
        return {"budget_ok": True, "next_action": "itinerary",
                "log": [f"Budget check PASSED: INR {total:,.0f} <= INR {budget:,.0f} ({total / budget:.0%} used)."]}
    if state.get("replan_count", 0) >= settings.max_replans:
        return {"budget_ok": False, "next_action": "itinerary",
                "log": [f"Budget check FAILED (INR {total:,.0f} > {budget:,.0f}) but re-plan limit reached; "
                        "presenting best plan found with a warning."]}
    return {"budget_ok": False, "next_action": "replan",
            "log": [f"Budget check FAILED: INR {total:,.0f} exceeds INR {budget:,.0f} by INR {total - budget:,.0f}. Re-planning."]}


# --------------------------------------------------------------------------- 7. re-plan
def _cheaper_hotel(state, target_saving: float):
    """Most comfortable cheaper hotel that closes the gap; else the cheapest cheaper one."""
    cur = state["hotel"]
    cost = state["cost"]
    per_night_factor = cost["nights"] * cost["rooms"]
    cheaper = [h for h in state["hotel_options"] if h["price_per_night_inr"] < cur["price_per_night_inr"] and h["rating"] >= 3.0]
    if not cheaper:
        return None
    closing = [h for h in cheaper if (cur["price_per_night_inr"] - h["price_per_night_inr"]) * per_night_factor >= target_saving]
    return max(closing, key=lambda h: (h["rating"], h["price_per_night_inr"])) if closing else cheaper[0]


def replan(state):
    feedback = state.get("feedback")
    count = state.get("replan_count", 0) + 1
    m = _metrics(state, replan_count=1)
    base = {"replan_count": count, "metrics": m, "feedback": ""}
    if feedback == "different_destination":
        return {**base, "excluded": state.get("excluded", []) + [state["destination"]["id"]], "next_action": "pick_destination",
                "log": [f"Re-plan #{count}: user asked for another destination; excluding {state['destination']['name']}."]}
    if feedback == "upgrade_hotel":
        better = [h for h in state["hotel_options"] if h["rating"] > state["hotel"]["rating"]]
        if better:
            h = min(better, key=lambda h: h["price_per_night_inr"])
            return {**base, "hotel": h, "next_action": "price_trip",
                    "log": [f"Re-plan #{count}: upgraded hotel to {h['name']} ({h['rating']}*)."]}
    gap = state["cost"]["total"] - state["request"]["budget"]
    if feedback == "cheaper":
        gap = max(gap, 0.15 * state["cost"]["total"])  # user wants it noticeably cheaper
    hotel = _cheaper_hotel(state, gap)
    if hotel and (gap <= 0 or (state["hotel"]["price_per_night_inr"] - hotel["price_per_night_inr"])
                  * state["cost"]["nights"] * state["cost"]["rooms"] >= gap * 0.5):
        return {**base, "hotel": hotel, "next_action": "price_trip",
                "log": [f"Re-plan #{count}: downgraded hotel to {hotel['name']} ({hotel['rating']}*, "
                        f"INR {hotel['price_per_night_inr']:,.0f}/night) to close a gap of INR {gap:,.0f}."]}
    return {**base, "excluded": state.get("excluded", []) + [state["destination"]["id"]], "next_action": "pick_destination",
            "log": [f"Re-plan #{count}: a cheaper hotel cannot close the INR {gap:,.0f} gap at "
                    f"{state['destination']['name']}; switching destination."]}


# --------------------------------------------------------------------------- 8. itinerary (LLM)
def itinerary(state):
    req, d = state["request"], get_destination(state["destination"]["id"])
    days = req["days"]
    start = dt.date.fromisoformat(state["flight"]["depart_date"])

    def fallback():
        acts, plan = d["attractions"], []
        queue = list(acts[1:])
        nxt = lambda: queue.pop(0) if queue else "Free time - revisit a favourite spot or relax"
        for i in range(days):
            date = (start + dt.timedelta(days=i)).isoformat()
            if i == 0:
                plan.append({"day": 1, "date": date, "title": "Arrival & settle in",
                             "morning": f"Fly {req['origin']['iata']} -> {d['iata']} ({state['flight']['airline']})",
                             "afternoon": f"Check in at {state['hotel']['name']}, rest", "evening": f"Easy walk: {acts[0]}"})
            elif i == days - 1:
                plan.append({"day": i + 1, "date": date, "title": "Departure",
                             "morning": "Breakfast, souvenir shopping", "afternoon": "Check out and head to the airport",
                             "evening": f"Fly back to {req['origin']['iata']}"})
            else:
                a, b = nxt(), nxt()
                plan.append({"day": i + 1, "date": date, "title": a, "morning": a, "afternoon": b,
                             "evening": "Local dinner - try regional specialities"})
        return plan

    def validate(obj):
        obj = obj.get("days", obj) if isinstance(obj, dict) else obj
        if not isinstance(obj, list) or len(obj) != days:
            raise ValueError("wrong number of days")
        out = []
        for i, day in enumerate(obj):
            out.append({"day": i + 1, "date": (start + dt.timedelta(days=i)).isoformat(),
                        **{k: str(day.get(k, ""))[:200] for k in ("title", "morning", "afternoon", "evening")}})
        return out

    plan, used_llm = ask_json(
        "You are an expert travel planner. Create a realistic day-by-day itinerary. Day 1 is arrival, the last day "
        "is departure. Output a JSON list of objects with keys: title, morning, afternoon, evening.",
        f"Destination: {d['name']}, {d['country']}. {days} days starting {start}. Travellers: {req['travellers']}. "
        f"Interests: {req['interests'] or 'general sightseeing'}. Hotel: {state['hotel']['name']}. "
        f"Season: {_climate(state).get('label', 'unknown')}. "
        f"Must-see options: {', '.join(d['attractions'])}.",
        validate, fallback)
    return {"itinerary": plan, "log": [f"Built a {days}-day itinerary ({'LLM' if used_llm else 'template'})."]}


# --------------------------------------------------------------------------- 9. human approval gate
def classify_feedback(text: str) -> str:
    t = text.lower()

    def fallback():
        if any(w in t for w in ("cheap", "expensive", "budget", "cost", "price", "less")):
            return "cheaper"
        if any(w in t for w in ("better hotel", "upgrade", "luxury", "nicer")):
            return "upgrade_hotel"
        return "different_destination"

    labels = ("cheaper", "upgrade_hotel", "different_destination")
    value, _ = ask_json(
        f"Classify the traveller's feedback on a trip plan into one of {list(labels)}. Output {{\"label\": ...}}.",
        text, lambda o: o["label"] if o.get("label") in labels else (_ for _ in ()).throw(ValueError()), fallback)
    return value


def approval(state):
    cost, dest, hotel = state["cost"], state["destination"], state["hotel"]
    signed = state.get("approved") or {}
    # After a sold-out swap, don't bother the human again unless the deal got worse.
    if signed.get("destination") == dest["id"] and signed.get("hotel_id") == hotel["hotel_id"] \
            and cost["total"] <= signed["total"]:
        return {"next_action": "book", "log": ["Plan changed but is not more expensive than what you approved; continuing."]}
    decision = interrupt({
        "type": "approval",
        "message": "Approve this plan? Booking happens only after your approval.",
        "destination": dest["name"], "reason": state["selection_reason"],
        "dates": f"{state['flight']['depart_date']} -> {state['flight']['return_date']}",
        "flight": f"{state['flight']['airline']} ({state['flight']['stops']} stop) INR {state['flight']['price_inr']:,.0f}",
        "hotel": f"{hotel['name']} {hotel['rating']}* INR {hotel['price_per_night_inr']:,.0f}/night",
        "cost": cost, "budget": state["request"]["budget"], "within_budget": state["budget_ok"],
        "itinerary": [f"Day {d['day']}: {d['title']}" for d in state["itinerary"]],
    })
    choice = str(decision.get("decision", "")).lower()
    if choice == "approve":
        return {"approved": {"destination": dest["id"], "hotel_id": hotel["hotel_id"], "total": cost["total"]},
                "next_action": "book", "log": [f"Human APPROVED the plan (INR {cost['total']:,.0f})."]}
    if choice == "cancel":
        return {"status": "cancelled", "next_action": "end", "log": ["Human cancelled the trip."]}
    if choice == "switch":
        target = next((c for c in state["candidates"] if c["id"] == decision.get("destination_id")), None)
        if target and target["id"] != dest["id"]:
            return {"override": target["id"], "next_action": "pick_destination",
                    "log": [f"Human asked to see {target['name']} instead of {dest['name']}."]}
        return {"next_action": "itinerary", "log": ["Ignored an invalid destination switch."]}
    action = classify_feedback(decision.get("feedback", ""))
    return {"feedback": action, "next_action": "replan",
            "log": [f"Human REJECTED the plan: {decision.get('feedback', '')!r} -> action '{action}'."]}


# --------------------------------------------------------------------------- 10. booking + recovery
def book(state):
    req, f = state["request"], state["flight"]
    res = tools.book_flight.invoke({"offer": f, "traveller": {}})
    if res["status"] == "error":  # not a sold-out offer: retrying other offers/destinations would not help
        return {"status": "booking_failed", "next_action": "end",
                "log": [f"Flight booking was rejected by the provider: {res['reason']}. Nothing was booked."]}
    if res["status"] != "confirmed":
        rest = [o for o in state["flight_options"] if o["offer_id"] != f["offer_id"]]
        m = _metrics(state, sold_out_count=1)
        if rest:
            nxt = rest[0]
            return {"flight_options": rest, "flight": nxt, "next_action": "price_trip", "metrics": m,
                    "log": [f"Flight booking failed ({res['reason']}). Switching to next offer: {nxt['airline']} "
                            f"INR {nxt['price_inr']:,.0f}; re-checking budget."]}
        return {"excluded": state.get("excluded", []) + [state["destination"]["id"]], "approved": {},
                "next_action": "pick_destination", "metrics": m,
                "log": ["All flight offers sold out; re-planning with another destination."]}
    hotel = tools.book_hotel.invoke({"hotel": state["hotel"], "check_in": f["depart_date"],
                                     "check_out": f["return_date"], "rooms": state["cost"]["rooms"]})
    return {"bookings": {"flight": {**res, "airline": f["airline"], "price_inr": f["price_inr"]}, "hotel": hotel},
            "next_action": "pdf", "status": "booked",
            "log": [f"Booked flight ({res['mode']}, ref {res['reference']}) and hotel ({hotel['mode']}, ref {hotel['reference']})."]}


# --------------------------------------------------------------------------- 11. pdf
def make_pdf(state):
    path = build_pdf(state)
    req, fm, hm = state["request"], state["flight_meta"], state["hotel_meta"]
    first = fm["first_found_price"] + hm["first_found_total"]
    final = state["cost"]["flights"] + state["cost"]["hotel"]
    metrics = _metrics(state, within_budget=state["cost"]["total"] <= req["budget"],
                       savings_vs_first_found=first - final, savings_pct=(first - final) / first if first else 0,
                       flight_savings_pct=(fm["first_found_price"] - state["cost"]["flights"]) / fm["first_found_price"],
                       date_shifted=bool(fm.get("chosen_offset")))
    return {"pdf_path": path, "status": "done", "metrics": metrics, "log": [f"Trip plan PDF written to {path}."]}
