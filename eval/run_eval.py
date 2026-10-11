"""Benchmark the agent on 20 varied trip requests and report resume-ready metrics.

    python -m eval.run_eval              # offline + deterministic (mock providers, no LLM)
    python -m eval.run_eval --use-llm    # same, but decisions/itineraries via the HuggingFace LLM
    python -m eval.run_eval --reject-first   # simulate a human rejecting the first plan ("too expensive")
"""
import argparse
import json
import os
import statistics
import time

from travel_agent.config import settings

ORIGINS = ["Delhi", "Mumbai", "Bangalore", "Kolkata", "Chennai", "Hyderabad", "Pune"]
INTERESTS = [["beach", "relax"], ["mountains", "adventure"], ["culture", "history"], ["food", "city"],
             ["spiritual", "nature"], ["beach", "nightlife"], ["snow", "mountains"]]
BUDGETS = [30000, 45000, 60000, 80000, 120000, 160000, 250000]
REGIONS = ["india", "abroad", "any", "india", "abroad"]


def requests_set(n=20):
    return [{
        "origin_city": ORIGINS[i % len(ORIGINS)], "region": REGIONS[i % len(REGIONS)],
        "month": (i * 5) % 12 + 1, "days": 3 + i % 6, "travellers": 1 + i % 4,
        "budget": BUDGETS[(i * 3) % len(BUDGETS)], "interests": INTERESTS[i % len(INTERESTS)],
    } for i in range(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--use-llm", action="store_true")
    ap.add_argument("--reject-first", action="store_true")
    args = ap.parse_args()
    if not args.use_llm:
        settings.llm_backend = "none"
    settings.use_open_meteo = False  # deterministic
    settings.duffel_token = settings.serpapi_key = ""  # benchmark must NEVER spend free API quota
    settings.output_dir = "eval/pdfs"

    from travel_agent.runner import run_trip

    rows = []
    for i, req in enumerate(requests_set(args.n), 1):
        rejected = {"done": not args.reject_first}

        def approve(plan):
            if not rejected["done"]:
                rejected["done"] = True
                return {"decision": "reject", "feedback": "too expensive, make it cheaper"}
            return {"decision": "approve"}

        t0 = time.time()
        s = run_trip(None, approve, request=req, on_log=lambda _: None)
        m = s.get("metrics", {})
        rows.append({
            "id": i, **{k: req[k] for k in ("origin_city", "region", "month", "days", "travellers", "budget")},
            "status": s.get("status"), "destination": s.get("destination", {}).get("name"),
            "total": s.get("cost", {}).get("total"), "within_budget": bool(m.get("within_budget")),
            "replans": m.get("replan_count", 0), "sold_out_recoveries": m.get("sold_out_count", 0),
            "savings_pct": m.get("savings_pct"), "flight_savings_pct": m.get("flight_savings_pct"),
            "date_shifted": m.get("date_shifted", False), "seconds": round(time.time() - t0, 2),
        })
        r = rows[-1]
        print(f"#{i:02d} {r['origin_city']:>9} -> {str(r['destination']):<28} {r['status']:<17} "
              f"INR {r['total'] or 0:>9,.0f} / {r['budget']:>9,.0f}  replans={r['replans']} soldout={r['sold_out_recoveries']}")

    done = [r for r in rows if r["status"] == "done"]
    pct = lambda xs: f"{100 * sum(xs) / len(xs):.0f}%" if xs else "n/a"
    summary = {
        "requests": len(rows),
        "plans_completed": pct([r["status"] == "done" for r in rows]),
        "within_budget": pct([r["within_budget"] for r in done]),
        "replanning_triggered": pct([r["replans"] > 0 for r in rows]),
        "avg_replans_when_triggered": round(statistics.mean([r["replans"] for r in rows if r["replans"]] or [0]), 2),
        "sold_out_recovered": sum(r["sold_out_recoveries"] for r in rows),
        "flex_dates_used": pct([r["date_shifted"] for r in done]),
        "avg_flight_savings_vs_first_found": f"{100 * statistics.mean([r['flight_savings_pct'] for r in done]):.1f}%",
        "avg_total_savings_vs_first_found": f"{100 * statistics.mean([r['savings_pct'] for r in done]):.1f}%",
        "avg_seconds_per_plan": round(statistics.mean(r["seconds"] for r in rows), 2),
        "llm": "HuggingFace" if args.use_llm else "rule-based",
    }
    os.makedirs("eval", exist_ok=True)
    with open("eval/results.json", "w") as f:
        json.dump({"summary": summary, "runs": rows}, f, indent=2)
    print("\n" + "\n".join(f"{k:<38} {v}" for k, v in summary.items()))
    print("\nSaved eval/results.json")


if __name__ == "__main__":
    main()
