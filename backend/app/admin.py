"""Owner admin CLI (ADR-0001 §11). Run only from the owner's interactive SSH session, never via the deploy bot.

python -m app.admin user-add --email me@example.com
python -m app.admin pair --email me@example.com --name "Apple Watch" [--replace <device_id>]
python -m app.admin runner-add --email ... --name vps --webhook-url https://relay.t3.codes/... \
    --secret-env VOX_RUNNER_SECRET_VPS [--alias servidor] [--default] [--trust cooperative]
python -m app.admin rule-add --email ... [--runner vps] [--project musiquillas] --mode granular \
    --effect deploy.production=ask --effect data.delete=deny [--hours 2]
python -m app.admin rule-list --email ... | rule-revoke --email ... --id <rule_id> | status --email ...
"""
import argparse
import os
import uuid
from datetime import timedelta

from sqlalchemy import select, update

from . import db
from .control import PAIR_TTL, new_pairing_code
from .policy import CATALOG, FLOOR
from .processor import apply_rule, audit, load_rules, sha256


def _user(conn, email):
    uid = conn.execute(select(db.users.c.id).where(db.users.c.email == email)).scalar()
    if uid is None:
        raise SystemExit(f"unknown user {email}")
    return uid


def _audit(conn, uid, event, subject_type, subject_id, payload=None):
    audit(conn, user_id=uid, event_type=event, subject_type=subject_type, subject_id=subject_id,
          actor_type="admin_cli", actor_id=os.environ.get("USER") or os.environ.get("USERNAME") or "owner",
          payload=payload or {})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vox-admin")
    ap.add_argument("--db", default=os.environ.get("VOX_DATABASE_URL", "sqlite:///./vox-dev.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ["user-add", "pair", "runner-add", "rule-add", "rule-list", "rule-revoke", "status"]:
        p = sub.add_parser(name)
        p.add_argument("--email", required=True)
        if name == "pair":
            p.add_argument("--name", default="Apple Watch")
            p.add_argument("--replace")
        if name == "runner-add":
            p.add_argument("--name", required=True)
            p.add_argument("--webhook-url", required=True)
            p.add_argument("--secret-env", required=True)
            p.add_argument("--alias", action="append", default=[])
            p.add_argument("--default", action="store_true")
            p.add_argument("--trust", choices=["isolated", "cooperative"], default="isolated")
        if name == "rule-add":
            p.add_argument("--runner")
            p.add_argument("--project")
            p.add_argument("--mode", choices=["bypass", "granular"], required=True)
            p.add_argument("--effect", action="append", default=[], help="capability=allow|ask|deny")
            p.add_argument("--hours", type=float)
        if name == "rule-revoke":
            p.add_argument("--id", required=True)
    args = ap.parse_args(argv)
    engine = db.make_engine(args.db)
    if engine.dialect.name == "sqlite":
        db.metadata.create_all(engine)  # dev only; PostgreSQL uses migrations/
    with engine.begin() as conn:
        if args.cmd == "user-add":
            uid = uuid.uuid4()
            conn.execute(db.users.insert().values(id=uid, email=args.email))
            print(uid)
            return
        uid = _user(conn, args.email)
        now = db.now()
        if args.cmd == "pair":
            code = new_pairing_code()
            replace = uuid.UUID(args.replace) if args.replace else None
            conn.execute(db.pairing_codes.insert().values(user_id=uid, code_hash=sha256(code), name=args.name,
                                                          replace_device_id=replace, expires_at=now + PAIR_TTL))
            _audit(conn, uid, "device.pairing_code_issued", "user", uid, {"replace": args.replace})
            print(f"{code}  (válido 10 min, un solo uso)")
        elif args.cmd == "runner-add":
            if not args.webhook_url.startswith("https://"):
                raise SystemExit("webhook must be https")
            if args.default:
                conn.execute(update(db.runners).where(db.runners.c.user_id == uid).values(is_default=False))
            rid = uuid.uuid4()
            conn.execute(db.runners.insert().values(id=rid, user_id=uid, name=args.name, aliases=args.alias,
                                                    webhook_url=args.webhook_url, secret_env=args.secret_env,
                                                    is_default=args.default, trust=args.trust))
            _audit(conn, uid, "runner.added", "runner", rid, {"name": args.name, "trust": args.trust})
            print(rid)
        elif args.cmd == "rule-add":
            effects = dict(e.split("=", 1) for e in args.effect)
            bad = [c for c, e in effects.items() if c not in CATALOG or e not in {"allow", "ask", "deny"}]
            if bad or FLOOR & {c for c, e in effects.items() if e != "deny"}:
                raise SystemExit(f"invalid or floor-lifting effects: {bad or sorted(FLOOR)}")
            runner_id = conn.execute(select(db.runners.c.id).where(db.runners.c.user_id == uid,
                                                                   db.runners.c.name == args.runner)).scalar() \
                if args.runner else None
            project_id = conn.execute(select(db.projects.c.id).where(db.projects.c.user_id == uid,
                                                                     db.projects.c.slug == args.project)).scalar() \
                if args.project else None
            if (args.runner and not runner_id) or (args.project and not project_id):
                raise SystemExit("unknown runner or project")
            print(apply_rule(conn, uid, runner_id=runner_id, project_id=project_id, mode=args.mode, effects=effects,
                             expires_at=now + timedelta(hours=args.hours) if args.hours else None, source="cli"))
        elif args.cmd == "rule-revoke":
            conn.execute(update(db.policy_rules).where(db.policy_rules.c.id == uuid.UUID(args.id),
                                                       db.policy_rules.c.user_id == uid).values(revoked_at=now))
            _audit(conn, uid, "policy.rule_revoked", "policy_rule", args.id)
        elif args.cmd == "rule-list":
            for r in load_rules(conn, uid):
                if r["revoked_at"] is None:
                    print(r["id"], r["mode"], r["runner_id"], r["project_id"], r["effects"], r["expires_at"])
        elif args.cmd == "status":
            paused = conn.execute(select(db.user_controls.c.paused_at).where(db.user_controls.c.user_id == uid)).scalar()
            active = conn.execute(select(db.agent_runs.c.id, db.agent_runs.c.state).where(
                db.agent_runs.c.user_id == uid, db.agent_runs.c.state.in_(["queued", "provisioning", "running"]))).all()
            print(f"paused={paused is not None} active_runs={len(active)}")


if __name__ == "__main__":
    main()
