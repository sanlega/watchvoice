import json
from datetime import timedelta

from sqlalchemy import select

from app import db
from app.runners import SYNC_EVENT, deliver_dispatches, sync_token

from test_runners import FakeRelay


def runner_id(env, name):
    with env.engine.connect() as c:
        return c.execute(select(db.runners.c.id).where(db.runners.c.name == name)).scalar_one()


def report(env, name, titles, token=None):
    token = token or sync_token(runner_id(env, name), db.now() + timedelta(hours=1))
    projects = [{"title": t, "folder": f"/home/u/{t}", "has_git": True, "has_origin": t != "Local"} for t in titles]
    return env.client.post(f"/v1/runners/{name}/projects", json={"projects": projects},
                           headers={"Authorization": f"Bearer {token}"})


def projects(env):
    with env.engine.connect() as c:
        rows = c.execute(select(db.projects.c.id, db.projects.c.name, db.projects.c.archived_at)).all()
        where = {}
        for pid, rname in c.execute(select(db.project_runners.c.project_id, db.runners.c.name)
                                    .join(db.runners, db.runners.c.id == db.project_runners.c.runner_id)):
            where.setdefault(pid, set()).add(rname)
    return {r.name: (r.archived_at is not None, where.get(r.id, set())) for r in rows}


def test_merge_creates_updates_and_archives_without_deleting(renv):
    r = report(renv, "mac", ["musiquillas", "Nuevo", "Local"])  # matches "Musiquillas" by normalized name
    assert r.status_code == 200 and r.json() == {"reported": 3, "created": 2, "archived": 0}
    got = projects(renv)
    assert got["Musiquillas"] == (False, {"mac"}) and got["Nuevo"] == (False, {"mac"})
    assert got["Hardware VOX"] == (False, set())  # never reported: untouched

    renv.submit("En Musiquillas añade modo multijugador")  # gives Musiquillas history
    r = report(renv, "mac", [])
    assert r.json() == {"reported": 0, "created": 0, "archived": 2}
    got = projects(renv)
    assert got["Nuevo"] == (True, set()) and got["Local"] == (True, set())
    assert got["Musiquillas"] == (False, set())  # has tasks: kept active
    assert renv.count(db.projects) == 4  # nothing deleted

    report(renv, "mac", ["Nuevo"])  # comes back
    assert projects(renv)["Nuevo"] == (False, {"mac"})


def test_project_on_several_runners_is_archived_only_when_no_machine_has_it(renv):
    report(renv, "mac", ["Compartido"])
    report(renv, "vps", ["Compartido"])
    assert projects(renv)["Compartido"] == (False, {"mac", "vps"})
    assert report(renv, "mac", []).json()["archived"] == 0
    assert projects(renv)["Compartido"] == (False, {"vps"})
    assert report(renv, "vps", []).json()["archived"] == 1
    assert projects(renv)["Compartido"] == (True, set())


def test_invalid_expired_or_foreign_token_is_rejected(renv):
    mac = runner_id(renv, "mac")
    assert report(renv, "mac", ["X"], token="nope").status_code == 401
    assert report(renv, "mac", ["X"], token=sync_token(mac, db.now() - timedelta(seconds=1))).status_code == 401
    assert report(renv, "vps", ["X"], token=sync_token(mac, db.now() + timedelta(hours=1))).status_code == 401
    good = sync_token(mac, db.now() + timedelta(hours=1))
    exp, mac_hex = good.split(".")
    assert report(renv, "mac", ["X"], token=f"{int(exp) + 3600}.{mac_hex}").status_code == 401  # expiry is MACed
    assert "X" not in projects(renv)


def test_selection_prefers_a_machine_that_has_the_project(renv):
    report(renv, "mac", ["Musiquillas"])  # vps is the user default but does not have it
    got = renv.get(renv.submit("En Musiquillas añade modo multijugador").json()["instruction_id"])
    assert got["status"] == "dispatched" and got["progress"]["runner"] == "mac"
    got = renv.get(renv.submit("En Musiquillas añade login en el servidor").json()["instruction_id"])
    assert got["progress"]["runner"] == "vps"  # named in the dictation wins
    report(renv, "vps", ["Musiquillas"])  # on both: the default one
    got = renv.get(renv.submit("En Musiquillas añade chat").json()["instruction_id"])
    assert got["progress"]["runner"] == "vps"


def test_sync_now_enqueues_one_signed_sync_per_runner(renv):
    for _ in range(2):  # pressing twice queues it once
        r = renv.client.post("/v1/runners/sync", headers=renv.headers())
        assert r.status_code == 202 and r.json() == {"runners": 2}
    relay = FakeRelay()
    assert deliver_dispatches(renv.engine, relay, base_url="https://vox.example") == 2
    payload = {json.loads(raw)["runner"]: json.loads(raw) for _, raw, _ in relay.calls}
    assert payload["mac"]["vox"] == SYNC_EVENT
    assert payload["mac"]["sync"]["url"] == "https://vox.example/v1/runners/mac/projects"
    assert report(renv, "mac", ["Desde webhook"], token=payload["mac"]["sync"]["token"]).status_code == 200
    assert deliver_dispatches(renv.engine, relay) == 0


def test_sync_now_requires_device(renv):
    assert renv.client.post("/v1/runners/sync").status_code == 401
