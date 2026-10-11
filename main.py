"""Interactive CLI for the AI travel planner.

    python main.py                 # asks you questions, then plans, waits for approval, books, writes a PDF
    python main.py --demo          # pre-filled request, still asks for approval
"""
import argparse
import datetime as dt
import textwrap

from travel_agent.data import ALL_TAGS
from travel_agent.llm import llm_name
from travel_agent.config import settings
from travel_agent.runner import run_trip

DEMO = {"origin_city": "Kolkata", "region": "any", "month": 12, "days": 5, "travellers": 2,
        "budget": 90000, "interests": ["beach", "food", "culture"]}


def ask(prompt, default=None, cast=str):
    while True:
        raw = input(f"{prompt}{f' [{default}]' if default is not None else ''}: ").strip()
        if not raw and default is not None:
            return default
        try:
            return cast(raw)
        except ValueError:
            print("  Please enter a valid value.")


def ask_intake():
    print("\n--- Plan a trip ---")
    return {
        "origin_city": ask("Departure city (e.g. Delhi, Mumbai, Kolkata)", "Delhi"),
        "region": ask("Travel within India or abroad? (india / abroad / any)", "any"),
        "month": ask("Month of travel (1-12)", (dt.date.today().month % 12) + 1, int),
        "start_day": ask("Preferred start day of month", 10, int),
        "days": ask("Trip length in days", 5, int),
        "travellers": ask("Number of travellers", 2, int),
        "budget": ask("Total budget in INR (everything included)", 80000, float),
        "interests": ask(f"Interests, comma separated ({', '.join(ALL_TAGS)})", "beach,food"),
    }


def ask_approval(plan):
    c = plan["cost"]
    print("\n" + "=" * 70)
    print(f" PROPOSED PLAN: {plan['destination']}   {plan['dates']}")
    print("=" * 70)
    print(textwrap.fill(plan["reason"], 70))
    print(f"\n Flight : {plan['flight']}\n Hotel  : {plan['hotel']}")
    print(f" Cost   : flights {c['flights']:,.0f} + hotel {c['hotel']:,.0f} + daily {c['daily_expenses']:,.0f}"
          f" + buffer {c['buffer']:,.0f} = INR {c['total']:,.0f}")
    print(f" Budget : INR {plan['budget']:,.0f} -> {'WITHIN budget' if plan['within_budget'] else 'OVER budget!'}")
    print(" Itinerary:\n   " + "\n   ".join(plan["itinerary"]))
    print("\n Nothing has been booked yet.")
    choice = ask("Approve and book? (y = approve, n = reject with feedback, c = cancel)", "y").lower()
    if choice.startswith("y"):
        return {"decision": "approve"}
    if choice.startswith("c"):
        return {"decision": "cancel"}
    return {"decision": "reject", "feedback": ask("What should change? (e.g. 'too expensive', 'somewhere else')")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="use a pre-filled trip request")
    args = ap.parse_args()
    print(f"LLM: {llm_name()} | flights: {settings.flight_provider} | hotels: {settings.hotel_provider}")
    final = run_trip(ask_intake, ask_approval, request=DEMO if args.demo else None,
                     on_log=lambda line: print(f"  > {line}"))
    print(f"\nStatus: {final.get('status')}")
    if final.get("pdf_path"):
        print(f"Your trip plan: {final['pdf_path']}")


if __name__ == "__main__":
    main()
