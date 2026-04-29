"""LangGraph workflow assembly for EnergyBridge."""

from langgraph.graph import END, START, StateGraph

from energybridge.agent.nodes import (
    node_actuate,
    node_control,
    node_explanation,
    node_generate_strategy,
    node_load_memory,
    node_logging,
    node_memory_update,
    node_parse_preference,
    node_safety,
    node_translate_grid,
)
from energybridge.agent.state import EnergyBridgeState


def build_energybridge_graph():
    graph = StateGraph(EnergyBridgeState)

    graph.add_node("load_memory", node_load_memory)
    graph.add_node("parse_preference", node_parse_preference)
    graph.add_node("translate_grid", node_translate_grid)
    graph.add_node("generate_strategy", node_generate_strategy)
    graph.add_node("control", node_control)
    graph.add_node("safety", node_safety)
    graph.add_node("actuate", node_actuate)
    graph.add_node("explanation", node_explanation)
    graph.add_node("memory_update", node_memory_update)
    graph.add_node("logging", node_logging)

    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "parse_preference")
    graph.add_edge("parse_preference", "translate_grid")
    graph.add_edge("translate_grid", "generate_strategy")
    graph.add_edge("generate_strategy", "control")
    graph.add_edge("control", "safety")
    graph.add_edge("safety", "actuate")
    graph.add_edge("actuate", "explanation")
    graph.add_edge("explanation", "memory_update")
    graph.add_edge("memory_update", "logging")
    graph.add_edge("logging", END)

    return graph.compile()
