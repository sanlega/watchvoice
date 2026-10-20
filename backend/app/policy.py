"""Server-side policy engine (A6, ADR-0001). Independent from the router/LLM.

Rules are keyed by runner x project (NULL = wildcard) with mode bypass | granular.
Most specific scope wins; equally specific rules -> most restrictive effect;
capabilities a rule does not mention fall through to the broader scope.
The transcript can only ADD requested capabilities (keyword detection); it can
never grant anything. Only layer (a) of ADR-0001 — runners not holding sensitive
credentials — is a security boundary; this engine decides what VOX itself does.
"""
import re
from dataclasses import dataclass
from datetime import datetime

from .router import norm

# issues.write is default: "open an issue for this idea" is a core simple task (2026-10-20, Santi).
DEFAULT_GRANTS = frozenset({"repo.read", "repo.write_branch", "repo.open_pr", "ci.run", "shell.sandboxed",
                            "issues.write"})
HIGH_RISK = frozenset({"deploy.production", "message.send", "secrets.write", "secrets.read_scoped",
                       "data.delete", "payment.send", "repo.write_protected", "repo.merge_protected",
                       "package.publish"})
# Non-bypassable floor (ADR-0001 §3). Lifting it requires a dashboard that does not exist yet.
FLOOR = frozenset({"secrets.exfiltrate", "backup.delete"})
CATALOG = DEFAULT_GRANTS | HIGH_RISK | FLOOR | {"network.http", "issues.write", "deploy.staging"}
EFFECT_ORDER = {"allow": 0, "ask": 1, "deny": 2}

SENSITIVE_PATTERNS = [
    (r"\b(despliega|desplegar|deploy|sube|publica|publicar|lanza)\b.*\b(produccion|prod|production)\b", "deploy.production"),
    (r"\b(produccion|production)\b.*\b(despliega|desplegar|deploy)\b", "deploy.production"),
    (r"\b(publica|publicar|publish)\b.*\b(version|release|paquete|npm|pypi|app store)\b", "package.publish"),
    (r"\b(borra|borrar|elimina|eliminar|drop|delete)\b.*\b(backup|backups|copia de seguridad|copias de seguridad)\b", "backup.delete"),
    (r"\b(borra|borrar|elimina|eliminar|drop|delete)\b.*\b(datos|base de datos|tabla|bucket|repo|repositorio|usuarios)\b", "data.delete"),
    (r"\b(paga|pagar|transfiere|transferir|compra|comprar)\b", "payment.send"),
    (r"\b(envia|enviar|manda|mandar|sube|subir|comparte|compartir)\b.*\b(secreto|secretos|credenciales|api keys?|tokens?)\b", "secrets.exfiltrate"),
    (r"\b(secreto|secretos|secret|secrets|credencial|credenciales|api key|token)\b", "secrets.write"),
    (r"\b(mergea|merge|fusiona)\b.*\b(main|master)\b", "repo.merge_protected"),
    (r"\b(push|commit|escribe)\b.*\b(main|master)\b", "repo.write_protected"),
    (r"\b(envia|enviar|manda|mandar)\b.*\b(email|correo|mensaje|tweet|slack)\b", "message.send"),
]


def requested_capabilities(text: str) -> set[str]:
    t = norm(text)
    return {cap for pattern, cap in SENSITIVE_PATTERNS if re.search(pattern, t)}


def risk_of(capability: str) -> str:
    return "high" if capability in HIGH_RISK | FLOOR else "medium"


@dataclass
class Decision:
    effect: str  # allow | ask | deny
    capability: str | None = None
    risk: str = "low"
    mode: str = "granular"  # effective mode of the most specific scope -> T3 runtime mode

    @property
    def runtime_mode(self) -> str:
        # Provisional, unverified mapping (ADR-0001 §1b): re-check with a spike per T3 version.
        return "full-access" if self.mode == "bypass" else "auto"


def _active(rule, now) -> bool:
    return rule["revoked_at"] is None and (rule["expires_at"] is None or rule["expires_at"] > now)


def _specificity(rule) -> int:
    return (2 if rule["project_id"] is not None else 0) + (1 if rule["runner_id"] is not None else 0)


def applicable(rules: list[dict], runner_id, project_id, now: datetime) -> list[dict]:
    return [r for r in rules if _active(r, now) and r["runner_id"] in (None, runner_id)
            and r["project_id"] in (None, project_id)]


def effect_for(capability: str, rules: list[dict]) -> str:
    """`rules` must already be filtered by `applicable`."""
    if capability in FLOOR:
        return "deny"
    for spec in sorted({_specificity(r) for r in rules}, reverse=True):
        effects = [("allow" if r["mode"] == "bypass" else r["effects"].get(capability))
                   for r in rules if _specificity(r) == spec]
        effects = [e for e in effects if e in EFFECT_ORDER]
        if effects:
            return max(effects, key=EFFECT_ORDER.__getitem__)
    return "allow" if capability in DEFAULT_GRANTS else "ask"


def scope_mode(rules: list[dict]) -> str:
    """Mode of the most specific scope that has rules; ties -> granular (more restrictive)."""
    if not rules:
        return "granular"
    top = max(_specificity(r) for r in rules)
    modes = {r["mode"] for r in rules if _specificity(r) == top}
    return "granular" if "granular" in modes else "bypass"


def evaluate(requested: set[str], rules: list[dict], runner_id, project_id, now: datetime) -> Decision:
    scoped = applicable(rules, runner_id, project_id, now)
    mode = scope_mode(scoped)
    worst = Decision("allow", mode=mode)
    for cap in sorted(requested):
        effect = effect_for(cap, scoped)
        if EFFECT_ORDER[effect] > EFFECT_ORDER[worst.effect] or (
                effect == worst.effect != "allow" and risk_of(cap) == "high" and worst.risk != "high"):
            worst = Decision(effect, cap, risk_of(cap), mode)
    return worst
