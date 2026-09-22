"""The model registry.

A routing option is one (model, effort) pair, called a variant. Its id is
"<model id>@<effort>", e.g. "claude-opus-5@high". Jev picks among variants,
so effort is chosen at the same time as the model rather than bolted on after.
"""
import json
import math
import os

from .client import PKG_DIR

REQUIRED = ("id", "provider", "display_name", "tier", "base_cost", "base_latency",
            "description", "efforts")
TIERS = ("fast", "balanced", "deep")
SCALE = ("low", "medium", "high")
SEP = "@"


class RegistryError(ValueError):
    pass


def load(path=None):
    """Return the validated registry dict."""
    path = path or os.environ.get("JEV_MODELS", os.path.join(PKG_DIR, "models.json"))
    with open(path) as f:
        reg = json.load(f)

    efforts = reg.get("efforts") or {}
    if not efforts:
        raise RegistryError("registry has no 'efforts' table")

    seen = set()
    for m in reg.get("models", []):
        missing = [k for k in REQUIRED if not m.get(k)]
        if missing:
            raise RegistryError(f"model {m.get('id', '?')!r} is missing {missing}")
        if m["tier"] not in TIERS:
            raise RegistryError(f"model {m['id']!r} has unknown tier {m['tier']!r}")
        for s in ("base_cost", "base_latency"):
            if m[s] not in SCALE:
                raise RegistryError(f"model {m['id']!r} has bad {s}={m[s]!r}")
        if SEP in m["id"]:
            raise RegistryError(f"model id {m['id']!r} must not contain {SEP!r}")
        for e in m["efforts"]:
            if e not in efforts:
                raise RegistryError(f"model {m['id']!r} uses unknown effort {e!r}")
        if m["id"] in seen:
            raise RegistryError(f"duplicate model id {m['id']!r}")
        seen.add(m["id"])

    if len(variants(reg)) < 2:
        raise RegistryError("Jev model-route needs at least two variants")
    return reg


def enabled(reg):
    return [m for m in reg["models"] if m.get("enabled", True)]


def by_id(reg, model_id):
    for m in reg["models"]:
        if m["id"] == model_id:
            return m
    return None


def split_variant(variant_id):
    """'claude-opus-5@high' -> ('claude-opus-5', 'high')."""
    base, _, effort = variant_id.partition(SEP)
    return base, (effort or None)


def _shift(level, step):
    """Move a low/medium/high level by `step`, clamped."""
    return SCALE[max(0, min(len(SCALE) - 1, SCALE.index(level) + step))]


def variants(reg, *, allow=None, min_context_tokens=None, efforts=None):
    """Every (model, effort) pair as a routing option.

    allow:               variant ids, or bare model ids to keep all their efforts
    min_context_tokens:  drop models whose context is known to be smaller
    efforts:             restrict to these effort levels
    """
    table = reg["efforts"]
    out = []
    for m in enabled(reg):
        ctx = m.get("context_tokens")
        if min_context_tokens and ctx is not None and ctx < min_context_tokens:
            continue
        for effort in m["efforts"]:
            if efforts and effort not in efforts:
                continue
            vid = f"{m['id']}{SEP}{effort}"
            if allow is not None and vid not in allow and m["id"] not in allow:
                continue
            step = table[effort].get("step", 0)
            out.append({
                "model": m,
                "effort": effort,
                "id": vid,
                "cost": _shift(m["base_cost"], step),
                "latency": _shift(m["base_latency"], step),
            })
    return out


def describe(variant):
    """The description Jev reads for one variant.

    Everything Jev needs to choose has to be in here: the model's measured
    strengths, its price, its usable context, and where it falls down.
    """
    m, effort = variant["model"], variant["effort"]
    bits = [f"{m['display_name']} at {effort} effort.", m["description"],
            f"Effort: {_effort_desc(m, effort)}."]

    price = m.get("price")
    if price:
        bits.append(f"Price ${price['input']}/${price['output']} per 1M tokens.")

    ctx = m.get("context_tokens")
    bits.append(f"Context {ctx:,} tokens." if ctx else "Context window unspecified.")
    if m.get("max_output_tokens"):
        bits.append(f"Max output {m['max_output_tokens']:,} tokens.")
    if m.get("tool_use"):
        bits.append("Supports tool use.")
    if m.get("strengths"):
        bits.append("Strong at: " + "; ".join(m["strengths"]) + ".")
    if m.get("avoid_when"):
        bits.append("Avoid when: " + "; ".join(m["avoid_when"]) + ".")
    return " ".join(bits)


def _effort_desc(model, effort):
    reg_efforts = model.get("_efforts_table") or {}
    return reg_efforts.get(effort, {}).get("description", effort)


def candidates(reg, *, allow=None, min_context_tokens=None, efforts=None):
    """Variants in the shape the Jev model-route endpoint expects."""
    vs = variants(reg, allow=allow, min_context_tokens=min_context_tokens,
                  efforts=efforts)
    for v in vs:  # let describe() reach the effort table
        v["model"] = dict(v["model"], _efforts_table=reg["efforts"])
    out = [{"id": v["id"], "description": describe(v),
            "cost": v["cost"], "latency": v["latency"]} for v in vs]
    if len(out) < 2:
        raise RegistryError(
            "need at least two candidates after filtering "
            f"(allow={allow}, min_context_tokens={min_context_tokens}, efforts={efforts})"
        )
    return out


TIER_ORDER = {"fast": 0, "balanced": 1, "deep": 2}


def floor_for(reg, kind, difficulty, input_tokens=None):
    """The capability floor for a (kind, difficulty) pair.

    difficulty is the 0-3 score from the Jev assessment, rounded to the nearest
    level: 2.73 asks for the level-3 floor, 2.1 stays at level 2. Rounding up
    would give anything above 2.0 the frontier floor.
    """
    table = reg.get("thresholds") or {}
    by_kind = table.get(kind) or table.get("general") or {}
    level = str(min(3, max(0, math.floor(difficulty + 0.5))))
    rule = dict(by_kind.get(level) or {})

    long_ctx = table.get("_long_context") or {}
    if input_tokens and input_tokens > (long_ctx.get("over_tokens") or float("inf")):
        require = dict(rule.get("require") or {})
        require.update(long_ctx.get("require") or {})
        rule["require"] = require
    return rule


def meets(variant, rule, efforts_table=None):
    """Does one variant clear the floor? Returns (ok, reason)."""
    m = variant["model"]

    min_tier = rule.get("min_tier")
    if min_tier and TIER_ORDER.get(m["tier"], 0) < TIER_ORDER.get(min_tier, 0):
        return False, f"tier {m['tier']} below {min_tier}"

    marks = m.get("benchmarks") or {}

    # For a kind with a specialist benchmark, not reporting it is evidence of
    # absence: a model that never published a browser score is not a browser
    # model. This is the deliberate exception to the rule below.
    for metric in rule.get("require_published") or []:
        if marks.get(metric) is None:
            return False, f"does not publish {metric}"

    for metric, minimum in (rule.get("require") or {}).items():
        score = marks.get(metric)
        # A model that does not publish the metric is unknown, not failing: no
        # code benchmark spans both model families, so absence cannot mean no.
        if score is None:
            continue
        if score < minimum:
            return False, f"{metric} {score} below {minimum}"

    # Effort is checked last so a capability shortfall, which is the more
    # useful explanation, is the one reported.
    min_effort = rule.get("min_effort")
    if min_effort and efforts_table:
        have = (efforts_table.get(variant["effort"]) or {}).get("step", 0)
        want = (efforts_table.get(min_effort) or {}).get("step", 0)
        if have < want:
            return False, f"effort {variant['effort']} below {min_effort}"

    return True, "clears the floor"


def qualified(reg, kind, difficulty, *, input_tokens=None, allow=None,
              min_context_tokens=None, efforts=None):
    """Variants that clear the capability floor, plus why each was cut.

    Cost plays no part here. It only breaks ties among what comes back.
    """
    rule = floor_for(reg, kind, difficulty, input_tokens)
    keep, cut = [], {}
    for v in variants(reg, allow=allow, min_context_tokens=min_context_tokens,
                      efforts=efforts):
        ok, why = meets(v, rule, reg["efforts"])
        if ok:
            keep.append(v)
        else:
            # Keep the first reason per model; an effort cut is less telling
            # than a capability cut, so a capability reason overwrites it.
            prev = cut.get(v["model"]["id"])
            if prev is None or prev.startswith("effort "):
                cut[v["model"]["id"]] = why
    # A model that survived at some effort is not excluded at all.
    for v in keep:
        cut.pop(v["model"]["id"], None)
    return keep, cut, rule


def variant_by_id(reg, variant_id):
    """Look up one variant by its '<model>@<effort>' id."""
    for v in variants(reg):
        if v["id"] == variant_id:
            return v
    return None
