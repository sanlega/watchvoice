"""Voice -> policy change proposals (ADR-0001 §4). Deterministic grammar only.

Loosen (bypass) needs an explicit scope and duration and a tap on the watch.
Tighten applies at once. Negations or mixed signals -> clarification, because
confusing loosen with tighten is the worst failure here.
"""
import re
from dataclasses import dataclass, field
from datetime import timedelta

from .policy import CATALOG, DEFAULT_GRANTS
from .router import norm

LOOSEN = re.compile(r"\b(saltate|saltarte|salta|ignora|quita|quitale|desactiva|bypass\w*)\b.*\b(permisos?|aprobaciones|reglas|confirmaciones)\b"
                    r"|\b(sin|no me) (pidas )?(permisos?|aprobaciones|confirmaciones)\b|\bmodo bypass\b")
TIGHTEN = re.compile(r"\b(pideme|pide|pedirme) (permiso|aprobacion|confirmacion)\b|\bmas control\b"
                     r"|\b(activa|reactiva|vuelve a activar) (los )?(permisos|aprobaciones)\b|\bmodo granular\b")
NEGATION = re.compile(r"\bno (te )?(saltes|ignores|quites|desactives)\b|\bya no\b|\bnunca\b|\bno quiero\b")
# Explicit only: "todos los permisos" must NOT widen the scope to everything.
ALL_SCOPE = re.compile(r"\ben (todas partes|todas las maquinas|todos los proyectos|todo)\b|\ben general\b|\bglobalmente\b")
DURATION = re.compile(r"\b(?:durante|por|para) (?:(\d+)|(una?|media)) (hora|horas|minuto|minutos|dia|dias)\b")
FOREVER = re.compile(r"\b(siempre|para siempre|permanente(mente)?|indefinidamente)\b")
WORDS = {"un": 1, "una": 1, "media": 0.5}
UNITS = {"hora": 3600, "horas": 3600, "minuto": 60, "minutos": 60, "dia": 86400, "dias": 86400}


@dataclass
class PolicyChange:
    direction: str  # loosen | tighten
    runner_id: object | None = None
    project_id: object | None = None
    duration: timedelta | None = None  # None = permanent
    summary: str = ""
    effects: dict = field(default_factory=dict)

    @property
    def risk(self) -> str:
        if self.direction == "tighten":
            return "low"
        return "high" if self.duration is None or (self.runner_id is None and self.project_id is None) else "medium"


@dataclass
class Clarify:
    error_code: str


def parse(text: str, runners: list, projects: list):
    """runners: rows with id/name/aliases; projects: ProjectRef. Returns PolicyChange, Clarify or None."""
    t = norm(text)
    loosen, tighten = bool(LOOSEN.search(t)), bool(TIGHTEN.search(t))
    negated = bool(NEGATION.search(t)) and bool(re.search(r"\b(permisos?|aprobaciones|reglas)\b", t))
    if not (loosen or tighten or negated):
        return None
    if negated or (loosen and tighten):
        return Clarify("policy_ambiguous")

    def mentioned(names):
        return any(re.search(rf"\b{re.escape(norm(n))}\b", t) for n in names if n.strip())

    runner_hits = [r for r in runners if mentioned([r.name, *(r.aliases or [])])]
    project_hits = [p for p in projects if mentioned([p.name, *p.aliases])]
    if len(runner_hits) > 1 or len(project_hits) > 1:
        return Clarify("policy_scope_ambiguous")
    runner = runner_hits[0] if runner_hits else None
    project = project_hits[0] if project_hits else None
    everywhere = bool(ALL_SCOPE.search(t))

    duration = None
    if m := DURATION.search(t):
        amount = float(m.group(1)) if m.group(1) else WORDS[m.group(2)]
        duration = timedelta(seconds=amount * UNITS[m.group(3)])
    forever = bool(FOREVER.search(t))

    if loosen:
        if not (runner or project or everywhere):
            return Clarify("policy_scope_missing")
        if duration is None and not forever:
            return Clarify("policy_duration_missing")
    scope = " · ".join(filter(None, [runner.name if runner else None, project.name if project else None])) \
        or "todas las máquinas y proyectos"
    when = "siempre" if duration is None else _fmt(duration)
    if loosen:
        return PolicyChange("loosen", runner.id if runner else None, project.id if project else None, duration,
                            f"Bypass · {scope} · {when}")
    effects = {cap: "ask" for cap in CATALOG - DEFAULT_GRANTS - {"repo.read"}}
    if re.search(r"\b(para )?todo\b", t):
        effects |= {cap: "ask" for cap in DEFAULT_GRANTS - {"repo.read"}}
    return PolicyChange("tighten", runner.id if runner else None, project.id if project else None, duration,
                        f"Pedir permiso · {scope} · {when}", effects)


def _fmt(d: timedelta) -> str:
    s = int(d.total_seconds())
    return f"{s // 3600} h" if s % 3600 == 0 else f"{s // 60} min"
