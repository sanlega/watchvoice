"""Routing with TypeSafe Jev (System One): one request answers, in parallel, what the user wants,
on which project, on which machine (runner) and with which coding agent. Jev returns typed choices
with probabilities, never generated text: the user's prompt is passed to the agent verbatim.

API: POST https://api.typesafe.ai/v1/systemone (docs.typesafe.ai/api). Env: TYPESAFE_API_KEY,
TYPESAFE_MODEL (default jev-latest). Thresholds are placeholders to calibrate on labelled dictations.
Policy is never decided here (policy.py / policy_voice.py stay deterministic).
"""
import logging
import os
import re
from dataclasses import dataclass

import httpx

from .router import NEW_PROJECT, ProjectRef, Route

log = logging.getLogger("vox.router_jev")
API = "https://api.typesafe.ai/v1/systemone"
CONFIDENT = 0.75  # shortcut: uncalibrated; measure on ~100 labelled dictations (PRD G3) before trusting
STATED = 0.5

INTENTS = {
    "implement_feature": "The user asks to build, change, fix, review or research something in an existing project",
    "continue_task": "The user asks to resume or continue earlier work ('sigue con', 'retoma', 'lo de ayer')",
    "new_project": "The user asks to create or start a new project",
    "capture_idea": "The user only wants an idea noted or saved, explicitly without executing anything",
    "status_query": "The user asks what happened, the status or progress of work, without asking for new work",
    "unclear": "The instruction is not understandable as a request to a coding assistant",
}


@dataclass
class Agent:
    key: str
    provider: str  # T3 providerInstanceId, e.g. claudeAgent, codex
    model: str
    description: str = ""


@dataclass
class RunnerRef:
    id: object
    name: str
    aliases: list
    agents: list  # list[Agent]


@dataclass
class JevDecision:
    route: Route
    runner_name: str | None  # named in the dictation, else None (caller applies defaults)
    runner_error: str | None
    agent_key: str | None  # named in the dictation, else None (runner default)
    confidence: float


class JevRouter:
    def __init__(self, api_key: str | None = None, model: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key or os.environ["TYPESAFE_API_KEY"]
        self.model = model or os.environ.get("TYPESAFE_MODEL", "jev-latest")
        self.client = client or httpx.Client(timeout=10)

    def questions(self, projects, runners, agents) -> dict:
        q = {
            "intent": {"type": "choice", "instructions": "What does the user want the coding assistant to do with "
                       "`instruction`?", "criteria": INTENTS},
            "names_project": {"type": "noul", "instructions": "Does `instruction` refer to one of `projects` by name, "
                              "alias or an unambiguous description?"},
            "names_machine": {"type": "noul", "instructions": "Does `instruction` say on which machine of `machines` "
                              "the work must run?"},
            "names_agent": {"type": "noul", "instructions": "Does `instruction` say which coding agent of `agents` "
                            "should do the work?"},
        }
        if projects:
            q["project"] = {"type": "choice", "instructions": "Which project in `projects` is `instruction` about?",
                            "criteria": {p.name: (p.description or p.name) + (f" (also called {', '.join(p.aliases)})"
                                                                              if p.aliases else "") for p in projects}}
        if runners:
            q["machine"] = {"type": "choice", "instructions": "On which machine in `machines` should the work run?",
                            "criteria": {r.name: f"Machine '{r.name}'" + (f", also called {', '.join(r.aliases)}"
                                                                          if r.aliases else "") for r in runners}}
        if agents:
            q["agent"] = {"type": "choice", "instructions": "Which coding agent in `agents` should do the work?",
                          "criteria": {a.key: a.description or a.key for a in agents.values()}}
        return q

    def ask(self, text, projects, runners, agents) -> dict:
        state = {"instruction": text,
                 "projects": [{"name": p.name, "aliases": p.aliases, "description": p.description} for p in projects],
                 "machines": [{"name": r.name, "aliases": r.aliases} for r in runners],
                 "agents": [{"key": a.key, "description": a.description} for a in agents.values()]}
        resp = self.client.post(API, headers={"Authorization": f"Bearer {self.api_key}"},
                                json={"model": self.model, "state": state,
                                      "questions": self.questions(projects, runners, agents)})
        resp.raise_for_status()
        return resp.json()["answers"]

    def decide(self, text: str, projects: list[ProjectRef], runners: list[RunnerRef]) -> JevDecision:
        agents = {a.key: a for r in runners for a in r.agents}
        a = self.ask(text, projects, runners, agents)
        intent = a["intent"]
        by_name = {p.name: p for p in projects}
        confidence = intent["confidence"]

        route = Route(intent["choice"], task_title=text.strip().rstrip(".")[:120], confidence=confidence,
                      model_version=f"typesafe:{self.model}", reason="jev")
        if intent["choice"] in {"unclear", "status_query"} or confidence < CONFIDENT:
            # shortcut: status queries are answered by the app's Activity view, not routed to agents yet
            route.needs_review, route.reason = True, "jev_unclear"
            if route.intent == "unclear":
                route.intent = "implement_feature"
        elif route.intent == "new_project":
            m = NEW_PROJECT.search(text)  # Jev selects, it does not generate: the name comes from the dictation
            name = re.split(r"[,.;:]| y ", m.group(1).strip(), maxsplit=1)[0].strip()[:80] if m else None
            route.new_project_name, route.needs_review = name, not name
            route.reason = "jev_new_project" if name else "project_name_missing"
        elif "project" in a and a["names_project"]["noul"] >= STATED and a["project"]["confidence"] >= CONFIDENT:
            route.project_id = by_name[a["project"]["choice"]].id
            confidence = min(confidence, a["project"]["confidence"])
        elif route.intent != "capture_idea":
            ranked = sorted(a.get("project", {}).get("probabilities", {}).items(), key=lambda kv: -kv[1])
            route.needs_review, route.reason = True, "jev_project_uncertain"
            route.candidates = [by_name[n] for n, _ in ranked[:2] if n in by_name]

        runner_name = runner_error = None
        if "machine" in a and a["names_machine"]["noul"] >= STATED:
            if a["machine"]["confidence"] >= CONFIDENT:
                runner_name = a["machine"]["choice"]
            else:
                runner_error = "ambiguous_runner"
        agent_key = a["agent"]["choice"] if "agent" in a and a["names_agent"]["noul"] >= STATED \
            and a["agent"]["confidence"] >= CONFIDENT else None
        log.info("jev_route", extra={"intent": route.intent, "status": route.reason, "code": runner_error})
        return JevDecision(route, runner_name, runner_error, agent_key, confidence)


def jev_from_env() -> JevRouter | None:
    return JevRouter() if os.environ.get("TYPESAFE_API_KEY") else None


def runner_refs(rows) -> list[RunnerRef]:
    return [RunnerRef(r.id, r.name, r.aliases or [], [Agent(**x) for x in (r.agents or [])]) for r in rows]


def agent_for(runner_row, key: str | None) -> tuple[dict | None, str | None]:
    """(agent, error). Named agent must exist on that runner; unnamed -> the runner's first agent, or None
    (T3's own default model)."""
    agents = runner_row.agents or []
    if key:
        chosen = next((x for x in agents if x["key"] == key), None)
        return (chosen, None) if chosen else (None, "agent_unavailable")
    return (agents[0] if agents else None), None
