"""Sensitivity classification: what does this content contain?

This module answers one question and nothing else:

    how sensitive is this content?      classify()

Sensitivity means what is *in* the content -- personal data, credentials --
never who is allowed to receive it. There are no vendors, models, targets or
approvals here. What a caller does with the answer is the caller's decision;
this module does not make it, and does not need to know it was made.

The judgement is Jev's: one typed `choice` decision over the classes in
classes.json, which comes back as a class plus its probabilities. It is a
class, not a list of findings -- Jev returns typed decisions, so there is no
honest way to make it enumerate which span of text gave it away.

Being probabilistic, it fails closed upwards: pass `min_confidence` and an
unsure answer is raised to the most sensitive class still in play rather than
rounded down to a comfortable one.
"""
import json
import os
from dataclasses import dataclass, field

from .client import DECISIONS_PATH, JevError, setting_source, post

PKG_DIR = os.path.dirname(os.path.abspath(__file__))

QUESTION = "data_class"

# The API caps a decision body at 32 KiB, shared with the class descriptions
# and the caller's context. Content past this is refused rather than trimmed:
# classifying the first half of something is how a regulated payload comes
# back "public".
MAX_CONTENT_BYTES = 16 * 1024


class PolicyError(ValueError):
    """The class file is unusable, or Jev's answer cannot be trusted."""


def classes_path():
    """Which class file is in force, and where that choice came from."""
    return setting_source("DATA_POLICY", os.path.join(PKG_DIR, "classes.json"))


def load(path=None):
    """Return the validated class ladder."""
    path = path or classes_path()[0]
    try:
        with open(path) as f:
            spec = json.load(f)
    except OSError as e:
        raise PolicyError(f"cannot read the class file {path}: {e}") from None
    except ValueError as e:
        raise PolicyError(f"{path} is not valid JSON: {e}") from None

    entries = spec.get("classes")
    if not entries:
        raise PolicyError(f"{path} has no 'classes'")
    if not spec.get("instructions"):
        raise PolicyError(f"{path} has no 'instructions' to ask Jev")

    seen = set()
    for entry in entries:
        if not entry.get("id"):
            raise PolicyError("a class has no id")
        if not entry.get("description"):
            raise PolicyError(
                f"class {entry['id']!r} has no description; the description is "
                f"the criteria Jev decides on, so an empty one decides nothing")
        if entry["id"] in seen:
            raise PolicyError(f"duplicate class id {entry['id']!r}")
        seen.add(entry["id"])
    return spec


def class_ids(spec=None):
    """The class ids, least to most sensitive."""
    return [c["id"] for c in (spec or load())["classes"]]


def rank(data_class, spec=None):
    """Position in the sensitivity order. Higher means more sensitive."""
    ids = class_ids(spec)
    try:
        return ids.index(data_class)
    except ValueError:
        raise PolicyError(
            f"unknown data class {data_class!r}; expected one of {ids}") from None


@dataclass
class Classification:
    """One answer, and enough of how it was reached to argue with it."""
    data_class: str
    confidence: float
    probabilities: dict = field(default_factory=dict)
    escalated_from: str = None      # set when min_confidence raised the class
    content_bytes: int = 0

    @property
    def escalated(self):
        return self.escalated_from is not None

    def at_least(self, data_class, spec=None):
        """True if this is at least as sensitive as `data_class`."""
        return rank(self.data_class, spec) >= rank(data_class, spec)

    def as_dict(self):
        return {"data_class": self.data_class, "confidence": self.confidence,
                "probabilities": self.probabilities,
                "escalated_from": self.escalated_from,
                "content_bytes": self.content_bytes}


# A native answer names its value after the question type -- a choice answer
# carries "choice" -- while the preset endpoints call it "decision".
VALUE_KEYS = ("choice", "decision")


def _answer(data, key):
    """Read one answer out of a decisions response. Accepts both shapes."""
    for src in ((data.get("answers") or {}).get(key), data):
        if not isinstance(src, dict):
            continue
        for vk in VALUE_KEYS:
            if src.get(vk) is not None:
                return dict(src, decision=src[vk])
    raise PolicyError(f"Jev returned no answer for {key!r}: {data}")


def payload(content, spec, context=None):
    """The decision body. Exposed so a caller can see exactly what is sent."""
    state = {"content": content}
    if context:
        state["context"] = context
    return {
        "state": state,
        "questions": {
            QUESTION: {
                "type": "choice",
                "instructions": spec["instructions"],
                "criteria": {c["id"]: c["description"] for c in spec["classes"]},
            }
        },
    }


def _escalate(ans_class, probabilities, spec):
    """The most sensitive class still in play. Used when confidence is low."""
    ids = class_ids(spec)
    live = [c for c in ids if (probabilities or {}).get(c, 0) > 0]
    # No probabilities at all means no evidence for anything, which is the
    # least safe moment to pick the bottom of the ladder.
    candidates = live or ids
    top = max(candidates, key=lambda c: rank(c, spec))
    return top if rank(top, spec) > rank(ans_class, spec) else ans_class


def classify(content, *, context=None, min_confidence=0.0, spec=None,
             backend=None, timeout=30, retries=3):
    """Classify how sensitive `content` is. Returns a Classification.

    content:         the text to judge. Note that classifying it sends it to
                     Jev -- if the content itself must not leave the machine,
                     pass a description of it instead of the thing.
    context:         anything else Jev should weigh, passed through as-is.
    min_confidence:  below this, the answer is raised to the most sensitive
                     class still carrying probability, and `escalated_from`
                     records what Jev actually said. 0.0 reports Jev verbatim.
    """
    spec = spec or load()
    if not isinstance(content, str) or not content.strip():
        raise PolicyError("nothing to classify: content is empty")

    size = len(content.encode())
    if size > MAX_CONTENT_BYTES:
        raise PolicyError(
            f"content is {size} bytes; this module classifies up to "
            f"{MAX_CONTENT_BYTES}. Split it and classify each part -- a "
            f"truncated payload is how a regulated one comes back public")
    if not 0.0 <= min_confidence <= 1.0:
        raise PolicyError(f"min_confidence must be in 0..1, got {min_confidence}")

    ans = _answer(post(DECISIONS_PATH, payload(content, spec, context),
                       timeout=timeout, retries=retries, backend=backend),
                  QUESTION)

    decided = ans.get("decision")
    ids = class_ids(spec)
    if decided not in ids:
        # Outside the ladder: refuse rather than hand back a class nobody
        # defined and no caller can act on.
        raise PolicyError(f"Jev returned unknown data class {decided!r}; "
                          f"expected one of {ids}")

    try:
        confidence = float(ans.get("confidence") or 0.0)
    except (TypeError, ValueError):
        raise PolicyError(f"Jev returned a non-numeric confidence "
                          f"{ans.get('confidence')!r}") from None
    probabilities = ans.get("probabilities")
    if not isinstance(probabilities, dict):
        # Unusable probabilities are no evidence for anything, which
        # escalation already treats as the least safe moment to round down.
        probabilities = {}
    final = decided
    if confidence < min_confidence:
        final = _escalate(decided, probabilities, spec)

    return Classification(
        data_class=final,
        confidence=confidence,
        probabilities=probabilities,
        escalated_from=decided if final != decided else None,
        content_bytes=size,
    )
