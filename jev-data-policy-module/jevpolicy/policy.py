"""Data classification: who is cleared to receive what.

This module knows nothing about models, routing or any particular vendor. It
answers two questions and nothing else:

    which targets may receive data of class X?      approved_targets()
    may this external service see this text?        assert_service()

Both are deterministic. Sensitivity is never a preference something else gets
to weigh -- a probabilistic answer is the wrong tool for where regulated data
may go.

The filter fails closed: anything absent from policy.json is public-only, so a
new target cannot silently inherit clearance.
"""
import json
import os

PKG_DIR = os.path.dirname(os.path.abspath(__file__))


class PolicyError(ValueError):
    """Base for every refusal this module makes."""


class NotApprovedError(PolicyError):
    """No target is cleared to receive data of the requested class."""


class ExternalLeakError(PolicyError):
    """Sending this text to an external service would exceed its clearance."""


def load(path=None):
    """Return the validated policy."""
    path = path or os.environ.get("JEV_DATA_POLICY",
                                  os.path.join(PKG_DIR, "policy.json"))
    with open(path) as f:
        pol = json.load(f)

    classes = pol.get("classes")
    if not classes:
        raise PolicyError("policy has no 'classes'")
    if pol.get("external_class_floor") and pol["external_class_floor"] not in classes:
        raise PolicyError(
            f"external_class_floor {pol['external_class_floor']!r} is not a known class")

    seen = set()
    for kind in ("targets", "services"):
        for entry in pol.get(kind) or []:
            if not entry.get("id"):
                raise PolicyError(f"a {kind[:-1]} has no id")
            key = (kind, entry["id"])
            if key in seen:
                raise PolicyError(f"duplicate {kind[:-1]} id {entry['id']!r}")
            seen.add(key)
            for c in entry.get("approved_classes") or []:
                if c not in classes:
                    raise PolicyError(
                        f"{entry['id']!r} approved for unknown class {c!r}")
    return pol


def rank(data_class, pol=None):
    """Position in the sensitivity order. Higher means more sensitive."""
    classes = (pol or load())["classes"]
    try:
        return classes.index(data_class)
    except ValueError:
        raise PolicyError(
            f"unknown data class {data_class!r}; expected one of {classes}") from None


def _entry(pol, kind, entry_id):
    for e in pol.get(kind) or []:
        if e["id"] == entry_id:
            return e
    return None


def _clears(entry, data_class, pol):
    """True if `entry` is cleared for `data_class`. Absent entry means no."""
    if entry is None:
        return rank(data_class, pol) == 0      # public only
    approved = entry.get("approved_classes") or ["public"]
    want = rank(data_class, pol)
    return any(rank(c, pol) >= want for c in approved)


def approved_targets(data_class, pol=None):
    """Ids of every target cleared to receive `data_class`.

    Feed this straight into a consumer's allow-list. An empty result is a
    real answer: nothing is cleared.
    """
    pol = pol or load()
    rank(data_class, pol)                       # validate
    return [t["id"] for t in pol.get("targets") or [] if _clears(t, data_class, pol)]


def assert_target(target_id, data_class, pol=None):
    """Raise unless `target_id` is cleared for `data_class`."""
    pol = pol or load()
    if not _clears(_entry(pol, "targets", target_id), data_class, pol):
        raise NotApprovedError(
            f"{target_id!r} is not approved for data class {data_class!r}. "
            f"Approval is an operator decision: raise approved_classes in "
            f"policy.json after your own review.")
    return True


def require_targets(data_class, minimum=1, pol=None):
    """Approved targets, or a clear refusal naming what is missing."""
    pol = pol or load()
    ok = approved_targets(data_class, pol)
    if len(ok) < minimum:
        raise NotApprovedError(
            f"{len(ok)} target(s) approved for data class {data_class!r}"
            f"{': ' + ', '.join(ok) if ok else ''}, need {minimum}. Approval is "
            f"an operator decision: raise approved_classes in policy.json after "
            f"your own review.")
    return ok


def assert_service(service_id, data_class, *, redacted=False, pol=None):
    """Raise unless text of `data_class` may be sent to `service_id`.

    An external service that merely coordinates work still receives whatever
    text you hand it. `redacted=True` is the caller asserting the text has
    been stripped of protected content -- it is a claim, not a check, so it
    belongs in an audit trail rather than a default.
    """
    pol = pol or load()
    entry = _entry(pol, "services", service_id)
    if _clears(entry, data_class, pol):
        return True

    floor = pol.get("external_class_floor")
    if redacted and floor and rank(data_class, pol) >= rank(floor, pol):
        return True

    receives = (entry or {}).get("receives", "the text you pass it")
    raise ExternalLeakError(
        f"{service_id!r} receives {receives}, and is not approved for data "
        f"class {data_class!r}. Pass redacted text and redacted=True, or "
        f"classify the call lower and keep the payload local.")


def guarded_call(fn, *, data_class, service, redacted=False, pol=None,
                 allow_kwarg="allow", minimum=2, **kwargs):
    """Run `fn` with its allow-list narrowed to approved targets.

    Dependency injection on purpose: this module never imports whatever `fn`
    belongs to, and that consumer never imports this one. The only contract is
    an allow-list keyword.
    """
    pol = pol or load()
    assert_service(service, data_class, redacted=redacted, pol=pol)
    kwargs[allow_kwarg] = require_targets(data_class, minimum=minimum, pol=pol)
    return fn(**kwargs)
