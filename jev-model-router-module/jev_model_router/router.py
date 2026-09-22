"""Model selection: ask Jev which (model, effort) variant should serve an input."""
from dataclasses import dataclass, field

from . import registry
from .client import post

STAKES = ("low", "medium", "high")
KINDS = ("code", "browser", "research", "writing", "general")

ASSESS_QUESTIONS = {
    "difficulty": {
        "type": "score",
        "instructions": "How hard is this task for a language model?",
        "criteria": [
            "Trivial: mechanical, one obvious answer",
            "Routine: ordinary work, well specified",
            "Hard: multi-step or under-specified",
            "Frontier: ambiguous and high consequence",
        ],
    },
    "kind": {
        "type": "choice",
        "instructions": "What kind of work is this?",
        "criteria": {
            "code": "Reading or writing code in a repository",
            "browser": "Driving a browser or a computer UI",
            "research": "Gathering and judging evidence",
            "writing": "Producing prose",
            "general": "None of the above",
        },
    },
    "ambiguous": {
        "type": "noul",
        "instructions": "Are the requirements under-specified?",
    },
}


@dataclass
class Assessment:
    """Stage one: what kind of work this is and how hard."""
    difficulty: float
    kind: str
    ambiguous: float
    confidence: float = 0.0

    def as_dict(self):
        return {"difficulty": self.difficulty, "kind": self.kind,
                "ambiguous": self.ambiguous, "confidence": self.confidence}


def assess(task, *, input_tokens=None, stakes=None):
    """Ask Jev how hard the task is and what kind of work it involves.

    This is the first of two calls. Its answer sets the capability floor, which
    is applied locally before the second call ever sees a candidate — so cost
    cannot trade against difficulty.
    """
    state = {"task": task}
    if input_tokens:
        state["input_tokens"] = input_tokens
    if stakes:
        state["stakes"] = stakes

    data = post("/api/v1/decisions", {"state": state, "questions": ASSESS_QUESTIONS})
    answers = data.get("answers") or {}
    diff = answers.get("difficulty") or {}
    kind = answers.get("kind") or {}
    return Assessment(
        difficulty=diff.get("score", 2.0),
        kind=kind.get("choice", "general"),
        ambiguous=(answers.get("ambiguous") or {}).get("noul", 0.0),
        confidence=diff.get("confidence", 0.0),
    )


@dataclass
class Selection:
    variant_id: str                  # "claude-opus-5@high"
    model_id: str                    # "claude-opus-5"
    effort: str                      # "high"
    confidence: float
    probabilities: dict = field(default_factory=dict)
    guidance: str = ""
    model: dict = field(default_factory=dict)   # the registry entry
    cost: str = ""
    latency: str = ""
    assessment: object = None       # stage one, when it ran
    floor: dict = field(default_factory=dict)      # the capability floor applied
    excluded: dict = field(default_factory=dict)   # model id -> why it was cut

    @property
    def display_name(self):
        return self.model.get("display_name", self.model_id)

    @property
    def label(self):
        return f"{self.display_name} @ {self.effort} effort"

    def as_dict(self):
        return {
            "variant_id": self.variant_id,
            "model_id": self.model_id,
            "effort": self.effort,
            "display_name": self.display_name,
            "provider": self.model.get("provider"),
            "tier": self.model.get("tier"),
            "context_tokens": self.model.get("context_tokens"),
            "cost": self.cost,
            "latency": self.latency,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "guidance": self.guidance,
            "assessment": self.assessment.as_dict() if self.assessment else None,
            "floor": self.floor,
            "excluded": self.excluded,
        }


def select_model(task, *, stakes="medium", priorities=None, constraints=None,
                 allow=None, min_context_tokens=None, efforts=None, reg=None,
                 input_tokens=None, kind=None, difficulty=None, assess_first=True):
    """Route `task` to a (model, effort) variant. Returns a Selection.

    Two stages. First Jev judges difficulty and kind; that sets a capability
    floor which is applied locally. Only the models that clear the floor reach
    the second call, so cost never competes with difficulty — it only breaks
    ties among candidates already able to do the work.

    stakes:             low | medium | high
    priorities:         ordered; cost belongs last
    constraints:        free-text limits, e.g. ["must run on-prem"]
    allow:              variant ids, or bare model ids to keep all their efforts
    min_context_tokens: drop models whose context is known to be smaller
    input_tokens:       size of the real input; implies min_context_tokens and
                        triggers the long-context recall gate
    kind, difficulty:   skip stage one by supplying its answer yourself
    assess_first:       False routes in one call, with no capability floor
    efforts:            restrict to these effort levels
    """
    if stakes not in STAKES:
        raise ValueError(f"stakes must be one of {STAKES}, got {stakes!r}")
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")

    reg = reg or registry.load()

    # An input of N tokens needs a window of at least N; the caller should not
    # have to know each model's window to say so.
    if input_tokens:
        min_context_tokens = max(min_context_tokens or 0, input_tokens)

    assessment, floor, excluded = None, {}, {}
    if assess_first and (kind is None or difficulty is None):
        assessment = assess(task, input_tokens=input_tokens, stakes=stakes)
        kind = kind or assessment.kind
        difficulty = assessment.difficulty if difficulty is None else difficulty

    if kind is not None and difficulty is not None:
        keep, excluded, floor = registry.qualified(
            reg, kind, difficulty, input_tokens=input_tokens, allow=allow,
            min_context_tokens=min_context_tokens, efforts=efforts)
        if not keep:
            raise ValueError(
                f"no model clears the floor {floor} for a {kind} task at "
                f"difficulty {difficulty}; excluded: {excluded}")
        allow = [v["id"] for v in keep]

        # One survivor is already the answer; a second call would add nothing.
        if len(keep) == 1:
            v = keep[0]
            return Selection(
                variant_id=v["id"], model_id=v["model"]["id"], effort=v["effort"],
                confidence=1.0, probabilities={v["id"]: 1.0},
                guidance="Only variant clearing the capability floor.",
                model=v["model"], cost=v["cost"], latency=v["latency"],
                assessment=assessment, floor=floor, excluded=excluded)

    cands = registry.candidates(
        reg, allow=allow, min_context_tokens=min_context_tokens, efforts=efforts
    )

    payload = {
        "task": task,
        "candidates": cands,
        "priorities": priorities or reg.get("default_priorities") or ["quality"],
        "stakes": stakes,
    }
    limits = list(constraints or [])
    if min_context_tokens:
        limits.append(f"The input needs at least {min_context_tokens:,} tokens of context.")
    if limits:
        payload["constraints"] = limits

    data = post("/api/v1/decisions/model-route", payload)
    variant_id = data.get("decision")

    variant = registry.variant_by_id(reg, variant_id)
    if variant is None:
        # Jev returned something outside the candidate list; refuse rather than
        # dispatch to a variant we know nothing about.
        raise ValueError(f"Jev returned unknown variant id {variant_id!r}")

    return Selection(
        variant_id=variant_id,
        model_id=variant["model"]["id"],
        effort=variant["effort"],
        confidence=data.get("confidence", 0.0),
        probabilities=data.get("probabilities") or {},
        guidance=data.get("guidance", ""),
        model=variant["model"],
        cost=variant["cost"],
        latency=variant["latency"],
        assessment=assessment,
        floor=floor,
        excluded=excluded,
    )


def guard_tool_call(tool, action, **kw):
    """Jev tool-guard: allow | confirm | review | deny for a consequential call."""
    payload = {"tool": tool, "action": action}
    for k in ("arguments_summary", "side_effects", "safeguards", "policy", "reversibility"):
        if kw.get(k):
            payload[k] = kw[k]
    return post("/api/v1/decisions/tool-guard", payload)


def review_completion(objective, **kw):
    """Jev completion check: complete | verify_more | incomplete."""
    payload = {"objective": objective}
    for k in ("completed_work", "verification", "known_gaps"):
        if kw.get(k):
            payload[k] = kw[k]
    return post("/api/v1/decisions/completion", payload)
