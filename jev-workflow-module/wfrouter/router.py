"""Workflow selection: ask Jev which workflow module should take a request.

This module decides and stops. It returns the chosen workflow's interface so
the host program can run it; it never runs anything itself, because the host
is the only thing that knows how its skills, agents and tools are invoked.

Routing is one Jev call while the whole catalogue fits comfortably in one
payload, and two -- stage first, then workflow inside it -- once it does not.
The second shape is not only about size: telling thirty-three near-neighbours
apart in one question is a harder question than two easy ones.
"""
import json
from dataclasses import dataclass, field

from . import registry
from .client import post

STAKES = ("low", "medium", "high")
QUESTION = "workflow"
STAGE_QUESTION = "stage"

# The API caps bodies at 32 KiB. Stay well under it: the request text and the
# caller's context share the payload with the candidate descriptions.
MAX_PAYLOAD_BYTES = 20 * 1024

INSTRUCTIONS = (
    "Pick the one workflow that should take this request next. Match what the "
    "request actually asks for against each workflow's own 'use when' and "
    "'do not use when'. Prefer the workflow that produces the thing being "
    "asked for over one that merely precedes it."
)

STAGE_INSTRUCTIONS = (
    "Pick the stage of production this request belongs to. Judge by what the "
    "request asks to have happen, not by which stage sounds most advanced: a "
    "request to decide something belongs to a deciding stage even late in a "
    "project, and a request to produce a file belongs to a producing stage."
)


@dataclass
class Route:
    """A decision, plus everything needed to act on it."""
    workflow_id: str
    command: str                     # "/coding-game"
    stage: str
    confidence: float
    probabilities: dict = field(default_factory=dict)
    guidance: str = ""
    workflow: dict = field(default_factory=dict)   # the manifest
    registry: dict = field(default_factory=dict, repr=False)
    context: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)      # how the decision was reached

    @property
    def display_name(self):
        return self.workflow.get("display_name", self.workflow_id)

    @property
    def implemented(self):
        return registry.implemented(self.workflow)

    @property
    def blocked_by(self):
        """Upstream workflows the caller reported as not done. Advisory."""
        return registry.blocked_by(self.registry, self.workflow_id, self.context)

    @property
    def interface(self):
        """What the host program executes. See registry.interface()."""
        return registry.interface(self.workflow, self.registry)

    def as_dict(self):
        return {
            "workflow": self.interface,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "guidance": self.guidance,
            "blocked_by": self.blocked_by,
            "steps": self.steps,
        }


# A native answer names its value after the question type -- a choice answer
# carries "choice" -- while the preset endpoints call it "decision".
VALUE_KEYS = ("choice", "decision")


def _answer(data, key):
    """Read one answer out of a decisions response, normalised to a decision.

    The native endpoint returns answers per question; the preset endpoints
    return a single decision at the top level. Accept both.
    """
    for src in ((data.get("answers") or {}).get(key), data):
        if not isinstance(src, dict):
            continue
        for vk in VALUE_KEYS:
            if src.get(vk) is not None:
                return dict(src, decision=src[vk])
    raise ValueError(f"Jev returned no answer for {key!r}: {data}")


def _payload(request, stakes, context, cands, question, instructions):
    state = {"request": request, "stakes": stakes}
    if context:
        state["context"] = context
    return {
        "state": state,
        "questions": {
            question: {
                "type": "choice",
                "instructions": instructions,
                "criteria": {c["id"]: c["description"] for c in cands},
            }
        },
    }


def payload_bytes(payload):
    return len(json.dumps(payload).encode())


def _ask(payload, question, valid):
    """Post one choice question and return its answer, or refuse the reply."""
    ans = _answer(post("/api/v1/decisions", payload), question)
    if ans.get("decision") not in valid:
        # Outside the candidate list: refuse rather than hand the host a target
        # it never offered.
        raise ValueError(f"Jev returned unknown {question} {ans.get('decision')!r}")
    return ans


def _step(question, ans):
    return {"question": question, "decision": ans["decision"],
            "confidence": ans.get("confidence", 0.0),
            "probabilities": ans.get("probabilities") or {}}


def select_stage(request, *, context=None, stakes="medium", reg=None):
    """Choose which stage of production a request belongs to.

    Useful on its own -- 'is this a design question or a build task' is a
    real question -- and it is the first half of a two-step route.
    """
    reg = reg or registry.load()
    cands = registry.stage_candidates(reg)
    payload = _payload(request, stakes, context, cands,
                       STAGE_QUESTION, STAGE_INSTRUCTIONS)
    return _ask(payload, STAGE_QUESTION, {c["id"] for c in cands})


def select_workflow(request, *, context=None, stage=None, allow=None,
                    stakes="medium", two_step=None, reg=None):
    """Route `request` to a workflow module. Returns a Route.

    context:   what the caller already knows. Keys that name a workflow are
               read as its state ("done", "missing") and reported back as
               `Route.blocked_by`; anything else is passed to Jev as-is.
    stage:     restrict to one stage
    allow:     restrict to these workflow ids or commands
    stakes:    low | medium | high -- how costly a wrong hand-off would be
    two_step:  choose the stage first. None picks automatically: two steps
               once one question would carry more than MAX_PAYLOAD_BYTES.
    """
    if stakes not in STAKES:
        raise ValueError(f"stakes must be one of {STAKES}, got {stakes!r}")

    reg = reg or registry.load()
    steps = []

    if stage is None and allow is None:
        if two_step is None:
            one_shot = _payload(request, stakes, context,
                                registry.candidates(reg), QUESTION, INSTRUCTIONS)
            two_step = payload_bytes(one_shot) > MAX_PAYLOAD_BYTES
        if two_step:
            ans = select_stage(request, context=context, stakes=stakes, reg=reg)
            stage = ans["decision"]
            steps.append(_step(STAGE_QUESTION, ans))

            only = registry.in_stage(reg, stage)
            if len(only) == 1:
                # Jev chose a stage holding one workflow. That is a decision it
                # made, not a filter the caller applied, so act on it rather
                # than ask a question with a single answer.
                return _route(reg, only[0], ans, context, steps)

    cands = registry.candidates(reg, stage=stage, allow=allow)
    ans = _ask(_payload(request, stakes, context, cands, QUESTION, INSTRUCTIONS),
               QUESTION, {c["id"] for c in cands})
    steps.append(_step(QUESTION, ans))
    return _route(reg, registry.by_id(reg, ans["decision"]), ans, context, steps)


def _route(reg, wf, ans, context, steps):
    return Route(
        workflow_id=wf["id"],
        command=wf["command"],
        stage=wf["stage"],
        confidence=ans.get("confidence", 0.0),
        probabilities=ans.get("probabilities") or {},
        guidance=ans.get("guidance", ""),
        workflow=wf,
        registry=reg,
        context=context or {},
        steps=steps,
    )
