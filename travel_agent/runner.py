"""Drive the graph: stream until an interrupt, ask a human (or a callback), resume."""
import uuid

from langgraph.types import Command

from .graph import build_graph


def run_trip(ask_intake, ask_approval, request: dict | None = None, on_log=print, max_steps: int = 60):
    """Run one trip end-to-end.

    ask_intake()        -> dict of raw answers (called only if `request` isn't given)
    ask_approval(plan)  -> {"decision": "approve"|"reject"|"cancel", "feedback": str}
    """
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": max_steps}
    from .nodes import normalize_request

    payload = {"request": normalize_request(request)} if request else {}
    seen = 0
    while True:
        for _ in graph.stream(payload, config, stream_mode="updates"):
            log = graph.get_state(config).values.get("log", [])
            for line in log[seen:]:
                on_log(line)
            seen = len(log)
        snap = graph.get_state(config)
        if not snap.interrupts:
            return snap.values
        question = snap.interrupts[0].value
        answer = ask_intake() if question["type"] == "intake" else ask_approval(question)
        payload = Command(resume=answer)
