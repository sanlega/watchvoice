"""Jev routing with a fake TypeSafe API (response shape from docs.typesafe.ai/api). No live calls."""
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app import db
from app.main import create_app
from app.processor import process_instruction
from app.router_jev import JevRouter
from app.runners import deliver_dispatches
from test_runners import FakeRelay

AGENTS = [{"key": "claude", "provider": "claudeAgent", "model": "claude-opus-5-5", "description": "Claude"},
          {"key": "codex", "provider": "codex", "model": "gpt-6.1-sol", "description": "Codex"}]


def choice(name, conf=0.9, others=()):
    probs = {name: conf, **{o: (1 - conf) / max(len(others), 1) for o in others}}
    return {"type": "choice", "choice": name, "confidence": conf, "probabilities": probs}


def noul(p):
    return {"type": "noul", "noul": p}


class FakeTypeSafe:
    def __init__(self, answers=None, status=200):
        self.answers, self.status, self.requests = answers or {}, status, []

    def handler(self, request: httpx.Request):
        self.requests.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer ts-test"
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "boom"})
        return httpx.Response(200, json={"model": "jev-latest", "answers": self.answers,
                                         "usage": {"input_tokens": 1, "output_tokens": 1}})

    def router(self):
        return JevRouter(api_key="ts-test", client=httpx.Client(transport=httpx.MockTransport(self.handler)))


def answers(intent="implement_feature", project="Musiquillas", machine="mac", agent="codex",
            names_project=0.95, names_machine=0.9, names_agent=0.9, project_conf=0.9, machine_conf=0.92):
    return {"intent": choice(intent, 0.93), "names_project": noul(names_project), "names_machine": noul(names_machine),
            "names_agent": noul(names_agent), "project": choice(project, project_conf, ["Hardware VOX"]),
            "machine": choice(machine, machine_conf, ["vps"]), "agent": choice(agent, 0.9, ["claude"])}


@pytest.fixture
def jenv(renv, monkeypatch):
    with renv.engine.begin() as c:
        c.execute(update(db.runners).values(agents=AGENTS))
    monkeypatch.setenv("TYPESAFE_API_KEY", "")  # no live calls from the inline background task
    return renv


def run(env, text, fake, **kw):
    """Submit without inline processing, then process with the fake Jev."""
    iid = env.client.post("/v1/instructions", json=env.body(text, **kw), headers=env.headers()).json()["instruction_id"]
    process_instruction(env.engine, uuid.UUID(iid), jev=fake.router())
    return env.get(iid)


@pytest.fixture
def no_inline(jenv):
    # Same database, API without inline processing, so each test drives the fake Jev itself.
    jenv.app = create_app(jenv.url, process_inline=False)
    jenv.client = TestClient(jenv.app)
    return jenv


def test_jev_selects_project_machine_and_agent(no_inline):
    fake = FakeTypeSafe(answers())
    got = run(no_inline, "Dile a Codex que en el Mac arregle el login de Musiquillas", fake)
    assert got["status"] == "dispatched" and got["project_id"] == str(no_inline.musiquillas)
    assert got["progress"]["runner"] == "mac" and got["progress"]["agent"] == "codex"
    sent = fake.requests[0]
    assert sent["model"] == "jev-latest" and sent["state"]["instruction"].startswith("Dile a Codex")
    assert {"intent", "project", "machine", "agent", "names_project", "names_machine", "names_agent"} \
        <= set(sent["questions"])
    assert {m["name"] for m in sent["state"]["machines"]} == {"vps", "mac"}
    relay = FakeRelay()
    deliver_dispatches(no_inline.engine, relay)
    payload = json.loads(relay.calls[0][1])
    assert payload["agent"] == {"key": "codex", "provider": "codex", "model": "gpt-6.1-sol", "description": "Codex"}
    assert payload["text"] == "Dile a Codex que en el Mac arregle el login de Musiquillas"  # prompt verbatim


def test_unnamed_machine_and_agent_use_defaults(no_inline):
    fake = FakeTypeSafe(answers(names_machine=0.1, names_agent=0.1))
    got = run(no_inline, "En Musiquillas añade modo oscuro", fake)
    assert got["progress"]["runner"] == "vps" and got["progress"]["agent"] == "claude"


def test_uncertain_project_asks_with_candidates(no_inline):
    fake = FakeTypeSafe(answers(project_conf=0.5))
    got = run(no_inline, "Arregla lo del login", fake)
    assert got["status"] == "needs_clarification" and got["error_code"] == "ambiguous_route"
    assert [c["name"] for c in got["candidates"]] == ["Musiquillas", "Hardware VOX"]
    assert no_inline.count(db.tasks) == 0


def test_uncertain_machine_and_missing_agent_ask(no_inline):
    got = run(no_inline, "En Musiquillas haz X en el equipo", FakeTypeSafe(answers(machine_conf=0.5)))
    assert got["error_code"] == "ambiguous_runner"
    with no_inline.engine.begin() as c:
        c.execute(update(db.runners).where(db.runners.c.name == "mac").values(agents=AGENTS[:1]))
    got = run(no_inline, "Dile a Codex que en el Mac haga X en Musiquillas", FakeTypeSafe(answers()))
    assert got["status"] == "needs_clarification" and got["error_code"] == "agent_unavailable"


def test_jev_outage_falls_back_to_rules(no_inline):
    got = run(no_inline, "En Musiquillas añade modo multijugador", FakeTypeSafe(status=500))
    assert got["status"] == "dispatched" and got["project_id"] == str(no_inline.musiquillas)


def test_policy_phrases_never_reach_the_model(no_inline):
    fake = FakeTypeSafe(answers())
    got = run(no_inline, "En el vps sáltate los permisos durante 2 horas", fake)
    assert got["intent"] == "policy_change" and fake.requests == []


def test_jev_cannot_bypass_policy(no_inline):
    got = run(no_inline, "En Musiquillas despliega a producción en el mac", FakeTypeSafe(answers()))
    assert got["status"] == "awaiting_approval"
