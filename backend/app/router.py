"""Hybrid project router: deterministic rules -> candidate retrieval -> optional LLM.

The router only *proposes*. It never grants permissions; policy.py decides.
Transcript text is untrusted data: nothing in it can select capabilities that
the server-side policy does not already allow.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Protocol

ROUTER_VERSION = "rules-v1"


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip()


@dataclass
class ProjectRef:
    id: object
    name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class Route:
    intent: str
    project_id: object | None = None
    new_project_name: str | None = None
    task_title: str | None = None
    confidence: float = 0.0
    needs_review: bool = False
    candidates: list[ProjectRef] = field(default_factory=list)
    reason: str = ""
    model_version: str = ROUTER_VERSION


class CandidateRetriever(Protocol):
    def candidates(self, text: str, projects: list[ProjectRef], k: int = 5) -> list[ProjectRef]: ...


class LLMRouter(Protocol):
    """Constrained-JSON router (PRD v0.2 §8 step 4). Must return a Route or None to abstain."""
    def route(self, text: str, candidates: list[ProjectRef]) -> Route | None: ...


class TokenOverlapRetriever:
    # shortcut: token overlap instead of Postgres FTS/trigram/pgvector; upgrade in ROUTE-01.
    def candidates(self, text, projects, k=5):
        words = set(re.findall(r"\w{4,}", norm(text)))
        scored = []
        for p in projects:
            pw = set(re.findall(r"\w{4,}", norm(" ".join([p.name, p.description, *p.aliases]))))
            if overlap := len(words & pw):
                scored.append((overlap, p.name, p))
        return [p for *_, p in sorted(scored, key=lambda s: (-s[0], s[1]))[:k]]


NEW_PROJECT = re.compile(r"\bcrea(?:r)?\s+(?:un\s+)?(?:nuevo\s+)?proyecto\s+(?:nuevo\s+)?(?:llamado|que se llame|con el nombre)\s+(.+)", re.I)
CAPTURE_ONLY = re.compile(r"\b(no (?:lo|la|los|las) ejecutes|no ejecutes|solo (?:anota|apunta)|anota esta idea|apunta esta idea"
                          r"|(?:anota|apunta)(?:lo)? en (?:mis|tus|las) notas)\b")
OPEN_ISSUE = re.compile(r"\b(abre|abrir|crea|crear|registra|mete|pon)\b.{0,40}\b(issue|incidencia|ticket)s?\b")
ANALYZE_BUG = re.compile(r"\b(analiza|analizar|investiga|investigar|revisa|mira|diagnostica)\b.{0,60}"
                         r"\b(bug|error|fallo|crash|problema|excepcion)\b")
CONTINUATION = re.compile(r"\b(sigue con|continua con|retoma)\b|\b(lo|eso) de ayer\b")


def _title(text: str) -> str:
    return text.strip().rstrip(".")[:120]


class HybridRouter:
    def __init__(self, retriever: CandidateRetriever | None = None, llm: LLMRouter | None = None):
        self.retriever = retriever or TokenOverlapRetriever()
        self.llm = llm

    def explicit_matches(self, text: str, projects: list[ProjectRef]) -> list[ProjectRef]:
        t = f" {norm(text)} "
        return [p for p in projects
                if any(re.search(rf"\b{re.escape(norm(a))}\b", t) for a in [p.name, *p.aliases] if a.strip())]

    def route(self, text: str, projects: list[ProjectRef], project_hint=None) -> Route:
        t = norm(text)
        if m := NEW_PROJECT.search(text):
            name = re.split(r"[,.;:]| y ", m.group(1).strip(), maxsplit=1)[0].strip()[:80]
            if name:
                return Route("new_project", new_project_name=name, task_title=_title(text), confidence=1.0,
                             reason="explicit_new_project")

        explicit = self.explicit_matches(text, projects)
        if project_hint is not None:
            hinted = [p for p in projects if p.id == project_hint]
            if not hinted:
                return Route("existing_project", needs_review=True, reason="hint_not_visible")
            if explicit and hinted[0] not in explicit:
                return Route("existing_project", needs_review=True, candidates=[hinted[0], *explicit],
                             reason="hint_conflicts_with_text")
            explicit = hinted

        if CAPTURE_ONLY.search(t):
            pid = explicit[0].id if len(explicit) == 1 else None
            return Route("capture_idea", project_id=pid, task_title=_title(text), confidence=1.0, reason="capture_only")

        intent = ("open_issue" if OPEN_ISSUE.search(t) else "analyze_bug" if ANALYZE_BUG.search(t)
                  else "continue_task" if CONTINUATION.search(t) else "implement_feature")
        if len(explicit) == 1:
            return Route(intent, project_id=explicit[0].id, task_title=_title(text), confidence=1.0,
                         reason="explicit_reference")
        if len(explicit) > 1:
            return Route(intent, needs_review=True, candidates=explicit[:5], reason="multiple_explicit_references")

        candidates = self.retriever.candidates(text, projects)
        if self.llm is not None:
            proposed = self.llm.route(text, candidates)
            # Validator: only accept projects that are visible candidates and confident enough.
            if proposed and proposed.project_id in {c.id for c in candidates} and proposed.confidence >= 0.90:
                proposed.needs_review = False
                return proposed
        return Route(intent, needs_review=True, candidates=candidates[:2] or projects[:2],
                     reason="ambiguous_continuation" if intent == "continue_task" else "no_confident_route")
