"""FastAPI backend for the React UI.

    uvicorn travel_agent.api:app --reload --port 8000

Endpoints stream NDJSON events so the UI can show the agent's reasoning live:
    {"type": "log", "text": "..."}            one per reasoning step
    {"type": "result", ...}                  final snapshot (awaiting approval / done / ...)
"""
import json
import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

from .config import settings
from .data import ALL_TAGS, DESTINATIONS, ORIGINS
from .graph import build_graph
from .llm import llm_name
from .nodes import normalize_request

app = FastAPI(title="AI Travel Planner")
GRAPH = build_graph()  # one compiled graph; each trip is its own checkpointer thread
FRONTEND = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")


class TripForm(BaseModel):
    origin_city: str
    region: str = "any"
    month: int = Field(ge=1, le=12)
    start_day: int = Field(10, ge=1, le=28)
    days: int = Field(5, ge=2, le=21)
    travellers: int = Field(2, ge=1, le=9)
    budget: float = Field(gt=0)
    interests: list[str] = []


class Decision(BaseModel):
    decision: str  # approve | reject | cancel
    feedback: str = ""
    destination_id: str = ""  # for decision == "switch"


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}


def _snapshot(thread_id: str) -> dict:
    snap = GRAPH.get_state(_config(thread_id))
    s = snap.values
    pending = snap.interrupts[0].value if snap.interrupts else None
    status = "awaiting_approval" if pending else s.get("status", "unknown")
    return {
        "type": "result", "thread_id": thread_id, "status": status, "approval": pending,
        "request": s.get("request"), "candidates": s.get("candidates", []),
        "destination": s.get("destination"), "reason": s.get("selection_reason"),
        "flight": s.get("flight"), "flight_meta": s.get("flight_meta"), "hotel": s.get("hotel"),
        "cost": s.get("cost"), "itinerary": s.get("itinerary", []), "bookings": s.get("bookings"),
        "metrics": s.get("metrics", {}), "log": s.get("log", []),
        "pdf_url": f"/api/trips/{thread_id}/pdf" if s.get("pdf_path") else None,
    }


def _stream(thread_id: str, payload):
    config = _config(thread_id)
    seen = len(GRAPH.get_state(config).values.get("log", []))
    try:
        for _ in GRAPH.stream(payload, config, stream_mode="updates"):
            log = GRAPH.get_state(config).values.get("log", [])
            for line in log[seen:]:
                yield json.dumps({"type": "log", "text": line}) + "\n"
            seen = len(log)
        yield json.dumps(_snapshot(thread_id)) + "\n"
    except Exception as exc:  # surface errors to the UI instead of a silently dropped stream
        yield json.dumps({"type": "error", "text": f"{type(exc).__name__}: {exc}"}) + "\n"


@app.get("/api/meta")
def meta():
    cities = sorted({name.title() for name, (code, *_rest) in ORIGINS.items()
                     if name not in ("new delhi", "bombay", "calcutta", "bengaluru")})
    return {"origins": cities, "interests": ALL_TAGS, "destinations": len(DESTINATIONS), "llm": llm_name(),
            "flight_provider": settings.flight_provider, "hotel_provider": settings.hotel_provider}


@app.post("/api/trips")
def start_trip(form: TripForm):
    try:
        request = normalize_request(form.model_dump())
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    thread_id = uuid.uuid4().hex
    return StreamingResponse(_stream(thread_id, {"request": request}), media_type="application/x-ndjson")


@app.post("/api/trips/{thread_id}/decision")
def decide(thread_id: str, body: Decision):
    snap = GRAPH.get_state(_config(thread_id))
    if not snap.interrupts:
        raise HTTPException(409, "This trip is not waiting for approval.")
    return StreamingResponse(_stream(thread_id, Command(resume=body.model_dump())),
                             media_type="application/x-ndjson")


@app.get("/api/trips/{thread_id}")
def get_trip(thread_id: str):
    if not GRAPH.get_state(_config(thread_id)).values:
        raise HTTPException(404, "Unknown trip")
    return _snapshot(thread_id)


@app.get("/api/trips/{thread_id}/pdf")
def get_pdf(thread_id: str):
    path = GRAPH.get_state(_config(thread_id)).values.get("pdf_path")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "PDF not ready")
    return FileResponse(path, media_type="application/pdf", filename=os.path.basename(path))


# Serve the built React app (frontend/dist) from the same server, if it has been built.
if os.path.isdir(FRONTEND):
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="ui")
