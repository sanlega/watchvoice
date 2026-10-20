"""Server-side policy engine (A6). Independent from the router/LLM.

Inputs: requested capabilities (derived from text by keyword detection: this can
only ADD requirements, never remove them) and server-side grants. The transcript
cannot grant anything: phrases like "tienes permiso" are ignored by design.
The agent runner must re-check every tool call at invocation time (PRD §9);
this instruction-time check is the first gate, not the only one.
"""
import re
from dataclasses import dataclass
from datetime import datetime

from .router import norm

DEFAULT_GRANTS = frozenset({"repo.read", "repo.write_branch", "repo.open_pr", "ci.run", "shell.sandboxed"})
HIGH_RISK = frozenset({"deploy.production", "message.send", "secrets.write", "secrets.read_scoped",
                       "data.delete", "payment.send", "repo.write_protected", "package.publish"})

SENSITIVE_PATTERNS = [
    (r"\b(despliega|desplegar|deploy|sube|publica|publicar|lanza)\b.*\b(produccion|prod|production)\b", "deploy.production"),
    (r"\b(produccion|production)\b.*\b(despliega|desplegar|deploy)\b", "deploy.production"),
    (r"\b(publica|publicar|publish)\b.*\b(version|release|paquete|npm|pypi|app store)\b", "package.publish"),
    (r"\b(borra|borrar|elimina|eliminar|drop|delete)\b.*\b(datos|base de datos|tabla|bucket|repo|repositorio|usuarios)\b", "data.delete"),
    (r"\b(paga|pagar|transfiere|transferir|compra|comprar)\b", "payment.send"),
    (r"\b(secreto|secretos|secret|secrets|credencial|credenciales|api key|token)\b", "secrets.write"),
    (r"\b(push|commit|escribe|merge|mergea)\b.*\b(main|master)\b", "repo.write_protected"),
    (r"\b(envia|enviar|manda|mandar)\b.*\b(email|correo|mensaje|tweet|slack)\b", "message.send"),
]


def requested_capabilities(text: str) -> set[str]:
    t = norm(text)
    return {cap for pattern, cap in SENSITIVE_PATTERNS if re.search(pattern, t)}


@dataclass
class Decision:
    effect: str  # allow | approval_required
    capability: str | None = None
    risk: str = "low"


def evaluate(requested: set[str], grants: list[dict], project_id, now: datetime) -> Decision:
    def standing(cap):
        return any(g["capability"] == cap and g["revoked_at"] is None
                   and (g["expires_at"] is None or g["expires_at"] > now)
                   and g["project_id"] in (None, project_id)
                   and (g["scope_json"] or {}).get("standing") is True
                   for g in grants)

    for cap in sorted(requested):
        if cap in DEFAULT_GRANTS:
            continue
        if cap in HIGH_RISK and standing(cap):
            continue
        # Unknown and high-risk capabilities default to an explicit human gate.
        return Decision("approval_required", cap, "high" if cap in HIGH_RISK else "medium")
    return Decision("allow")
