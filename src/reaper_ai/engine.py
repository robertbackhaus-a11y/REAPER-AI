"""REAPER-AI: initial LangGraph execution flow."""

from typing import Literal, TypedDict

import httpx
from langgraph.graph import StateGraph, START, END

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.6:35b"


class AgentState(TypedDict):
    task: str
    plan: str
    response: str
    status: str
    error: str


def ask_ollama(system: str, user: str) -> str:
    with httpx.Client(timeout=300) as client:
        result = client.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "think": False,
                "options": {"num_ctx": 8192},
            },
        )
        result.raise_for_status()
        return result.json()["message"]["content"].strip()


def analyze(state: AgentState) -> dict:
    print("[ANALYZE]")
    plan = ask_ollama(
        "Du bist der Planer eines Coding-Agenten. "
        "Beschreibe in maximal drei kurzen Schritten, "
        "wie die Aufgabe zu bearbeiten ist. "
        "Keine erfundenen Ergebnisse.",
        state["task"],
    )
    return {"plan": plan, "status": "analyzed"}


def execute(state: AgentState) -> dict:
    print("[EXECUTE]")
    response = ask_ollama(
        "Du bist die Ausführungskomponente von REAPER-AI. "
        "Bearbeite die Aufgabe anhand des Plans. "
        "Du besitzt derzeit keine Datei- oder Shell-Tools. "
        "Behaupte niemals, Dateien geändert oder Tests ausgeführt zu haben.",
        f"Auftrag:\n{state['task']}\n\nPlan:\n{state['plan']}",
    )
    return {"response": response, "status": "executed"}


def verify(state: AgentState) -> dict:
    print("[VERIFY]")
    if not state["response"]:
        return {
            "status": "failed",
            "error": "Modell hat kein Ergebnis geliefert.",
        }

    return {"status": "completed", "error": ""}


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("analyze", analyze)
    graph.add_node("execute", execute)
    graph.add_node("verify", verify)

    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "execute")
    graph.add_edge("execute", "verify")
    graph.add_edge("verify", END)

    return graph.compile()


if __name__ == "__main__":
    agent = build_graph()

    result = agent.invoke({
        "task": "Erkläre auf Deutsch in zwei Sätzen, warum ein Coding-Agent seine Änderungen verifizieren muss.",
        "plan": "",
        "response": "",
        "status": "pending",
        "error": "",
    })

    print("\nSTATUS:", result["status"])
    print("PLAN:", result["plan"])
    print("ERGEBNIS:", result["response"])
    if result["error"]:
        print("FEHLER:", result["error"])
