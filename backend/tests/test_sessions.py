"""Simple tasks open a NEW T3 session in the right project; replies continue the SAME conversation."""
import json
import uuid

from sqlalchemy import select

from app import db
from app.admin import main as admin
from app.runners import DISPATCH_EVENT, MESSAGE_EVENT, deliver_dispatches
from test_runners import FakeRelay


def dispatched(env, text, **kw):
    got = env.get(env.submit(text, **kw).json()["instruction_id"])
    relay = FakeRelay()
    deliver_dispatches(env.engine, relay, base_url="https://vox.example")
    return got, [json.loads(c[1]) for c in relay.calls]


def start(env, payload, thread="thread-42"):
    auth = {"Authorization": f"Bearer {payload['callback']['token']}"}
    r = env.client.post(f"/v1/runs/{payload['run_id']}/events", json={"event": "started", "external_ref": thread},
                        headers=auth)
    assert r.status_code == 204
    return auth


def test_open_issue_is_a_new_session_with_task_kind(renv):
    got, [p] = dispatched(renv, "Abre un issue en Musiquillas: idea de modo karaoke con amigos")
    assert got["status"] == "dispatched" and got["intent"] == "open_issue"  # issues.write needs no approval
    assert p["vox"] == DISPATCH_EVENT and p["task_kind"] == "open_issue"
    assert p["project"] == {"name": "Musiquillas", "slug": "musiquillas", "repo_url": None, "create": False}


def test_analyze_bug_intent(renv):
    got, [p] = dispatched(renv, "Analiza el bug del login que falla en Musiquillas")
    assert p["task_kind"] == "analyze_bug"


def test_note_goes_to_notes_project_or_stays_stored(renv):
    got, calls = dispatched(renv, "Apunta esta idea en mis notas: un metrónomo que vibre en el reloj")
    assert got["status"] == "captured" and calls == []  # no notes project yet
    admin(["--db", renv.url, "project-add", "--email", "owner@example.invalid", "--name", "Notas", "--notes"])
    got, [p] = dispatched(renv, "Apunta esta idea en mis notas: un afinador por voz")
    assert got["status"] == "dispatched" and p["task_kind"] == "capture_idea" and p["project"]["name"] == "Notas"


def test_new_project_asks_runner_to_create_it(renv):
    got, [p] = dispatched(renv, "Crea un proyecto llamado Ideas Locas y prepara un README")
    assert p["project"]["name"] == "Ideas Locas" and p["project"]["create"] is True


def test_reply_continues_the_same_conversation(renv):
    first, [p] = dispatched(renv, "Analiza el bug del login en Musiquillas")
    auth = start(renv, p)
    reply, [m] = dispatched(renv, "Ahora abre un issue con lo que has encontrado",
                            reply_to_run_id=first["agent_run_id"])
    assert reply["status"] == "dispatched" and reply["task_id"] == first["task_id"]
    assert reply["agent_run_id"] != first["agent_run_id"]
    assert m["vox"] == MESSAGE_EVENT and m["external_ref"] == "thread-42"
    assert m["text"] == "Ahora abre un issue con lo que has encontrado" and m["run_id"] == reply["agent_run_id"]
    # the earlier attempt is superseded, so a timeout cannot cancel the thread that is now working on the reply
    with renv.engine.connect() as c:
        states = dict(c.execute(select(db.agent_runs.c.id, db.agent_runs.c.state)).all())
    assert states[uuid.UUID(first["agent_run_id"])] == "cancelled"
    assert renv.client.post(f"/v1/runs/{p['run_id']}/events", json={"event": "progress"}, headers=auth) \
        .status_code == 409
    new_auth = {"Authorization": f"Bearer {m['callback']['token']}"}
    assert renv.client.post(f"/v1/runs/{m['run_id']}/events", json={"event": "succeeded"},
                            headers=new_auth).status_code == 204


def test_voice_continue_reuses_latest_conversation_of_that_project(renv):
    first, [p] = dispatched(renv, "Analiza el bug del login en Musiquillas")
    start(renv, p)
    cont, [m] = dispatched(renv, "Sigue con lo de Musiquillas y propón un arreglo")
    assert cont["task_id"] == first["task_id"] and m["vox"] == MESSAGE_EVENT and m["external_ref"] == "thread-42"


def test_reply_to_unstarted_or_foreign_run_asks(renv):
    first, _ = dispatched(renv, "Analiza el bug del login en Musiquillas")  # never reported "started"
    got, calls = dispatched(renv, "y mira también el registro", reply_to_run_id=first["agent_run_id"])
    assert got["error_code"] == "conversation_not_found" and calls == []
    got, _ = dispatched(renv, "hola", reply_to_run_id=str(uuid.uuid4()))
    assert got["error_code"] == "conversation_not_found"


def test_sensitive_reply_needs_approval_then_goes_to_same_thread(renv):
    first, [p] = dispatched(renv, "Analiza el bug del login en Musiquillas")
    start(renv, p)
    reply, calls = dispatched(renv, "Vale, despliega a producción", reply_to_run_id=first["agent_run_id"])
    assert reply["status"] == "awaiting_approval" and calls == []
    assert renv.decide(renv.pending()[0]).status_code == 200
    relay = FakeRelay()
    deliver_dispatches(renv.engine, relay)
    assert json.loads(relay.calls[0][1])["vox"] == MESSAGE_EVENT
