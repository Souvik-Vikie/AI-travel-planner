"""LangGraph wiring: an autonomous planning loop with a budget critic, re-planning,
booking-failure recovery and exactly one human approval gate before any booking."""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import TravelState


def _route(state) -> str:
    return state["next_action"]


def build_graph(checkpointer=None):
    g = StateGraph(TravelState)
    g.add_node("intake", nodes.intake)                    # human: trip preferences
    g.add_node("research", nodes.research)                # tool: rank destinations
    g.add_node("pick_destination", nodes.pick_destination)  # LLM decision
    g.add_node("search_flights", nodes.search_flights)    # tool: +/- N day flexible search
    g.add_node("search_hotels", nodes.search_hotels)      # tool
    g.add_node("price_trip", nodes.price_trip)
    g.add_node("budget_critic", nodes.budget_critic)
    g.add_node("replan", nodes.replan)
    g.add_node("itinerary", nodes.itinerary)              # LLM
    g.add_node("approval", nodes.approval)                # human: the single gate before money moves
    g.add_node("book", nodes.book)                        # tool: flight + hotel booking
    g.add_node("make_pdf", nodes.make_pdf)

    g.add_edge(START, "intake")
    g.add_edge("intake", "research")
    g.add_edge("research", "pick_destination")
    g.add_conditional_edges("pick_destination", _route, {"search": "search_flights", "end": END})
    g.add_edge("search_flights", "search_hotels")
    g.add_edge("search_hotels", "price_trip")
    g.add_edge("price_trip", "budget_critic")
    g.add_conditional_edges("budget_critic", _route, {"itinerary": "itinerary", "replan": "replan"})
    g.add_conditional_edges("replan", _route, {"price_trip": "price_trip", "pick_destination": "pick_destination"})
    g.add_edge("itinerary", "approval")
    g.add_conditional_edges("approval", _route, {"book": "book", "replan": "replan", "end": END,
                                                "pick_destination": "pick_destination", "itinerary": "itinerary"})
    g.add_conditional_edges("book", _route, {"pdf": "make_pdf", "price_trip": "price_trip",
                                             "pick_destination": "pick_destination", "end": END})
    g.add_edge("make_pdf", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
