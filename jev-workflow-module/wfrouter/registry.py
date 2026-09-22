"""The workflow registry.

One workflow is one directory under `workflows/`, holding a `module.json` that
declares its interface. Nothing else here knows what a workflow *does* -- the
directory is where that gets written later (system prompt, skill, tools), and
splitting it per directory is what lets each one be maintained on its own.

A directory is discovered, never registered: dropping a new folder in adds a
routing option, and deleting one removes it.
"""
import json
import os

from .client import PKG_DIR

MANIFEST = "module.json"
REQUIRED = ("id", "command", "stage", "display_name", "description")
ENTRY_KINDS = ("skill", "agent", "command", "workflow")


class RegistryError(ValueError):
    pass


def workflows_dir(path=None):
    return path or os.environ.get("WORKFLOW_MODULES",
                                  os.path.join(PKG_DIR, "workflows"))


def load(path=None):
    """Discover and validate every workflow module. Returns the registry dict."""
    root = workflows_dir(path)
    try:
        with open(os.path.join(root, "stages.json")) as f:
            stages = json.load(f)["stages"]
    except OSError as e:
        raise RegistryError(f"no stages.json in {root}: {e}") from None

    mods, seen_cmd = [], {}
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        manifest = os.path.join(d, MANIFEST)
        if not os.path.isfile(manifest):
            continue
        with open(manifest) as f:
            try:
                m = json.load(f)
            except json.JSONDecodeError as e:
                raise RegistryError(f"{manifest}: {e}") from None

        missing = [k for k in REQUIRED if not m.get(k)]
        if missing:
            raise RegistryError(f"{manifest} is missing {missing}")
        if m["id"] != name:
            raise RegistryError(
                f"{manifest} declares id {m['id']!r} but lives in {name!r}; "
                "the directory name is the id")
        if m["command"] != f"/{m['id']}":
            raise RegistryError(
                f"{m['id']}: command {m['command']!r} should be '/{m['id']}'")
        if m["stage"] not in stages:
            raise RegistryError(f"{m['id']}: unknown stage {m['stage']!r}")
        if m["command"] in seen_cmd:
            raise RegistryError(f"duplicate command {m['command']!r}")
        seen_cmd[m["command"]] = m["id"]

        entry = m.get("entry") or {}
        if entry.get("kind") and entry["kind"] not in ENTRY_KINDS:
            raise RegistryError(
                f"{m['id']}: entry.kind {entry['kind']!r} not in {ENTRY_KINDS}")

        m["dir"] = d
        mods.append(m)

    ids = {m["id"] for m in mods}
    for m in mods:
        unknown = [d for d in m.get("depends_on") or [] if d not in ids]
        if unknown:
            raise RegistryError(f"{m['id']}: depends_on unknown workflow {unknown}")

    if len(enabled(mods)) < 2:
        raise RegistryError(f"need at least two enabled workflows in {root}")
    return {"root": root, "stages": stages, "workflows": mods}


def enabled(mods):
    mods = mods["workflows"] if isinstance(mods, dict) else mods
    return [m for m in mods if m.get("enabled", True)]


def by_id(reg, workflow_id):
    for m in reg["workflows"]:
        if m["id"] == workflow_id:
            return m
    return None


def implemented(m):
    """True once the directory holds something the host can actually run.

    An interface with nothing behind it is still routable on purpose: the
    router's job is to name the target, and an empty target is a visible gap
    rather than a silent one.
    """
    entry = m.get("entry") or {}
    if entry.get("ref"):
        return True
    prompt = system_prompt_path(m)
    return bool(prompt and os.path.getsize(prompt) > 0 and "TODO"
                not in open(prompt).read())


def system_prompt_path(m):
    """Absolute path to the workflow's system prompt, or None if unset/absent."""
    name = (m.get("entry") or {}).get("system_prompt")
    if not name:
        return None
    path = os.path.join(m["dir"], name)
    return path if os.path.isfile(path) else None


def describe(m):
    """The text Jev reads when deciding whether this workflow fits.

    Everything that separates it from its neighbours has to be in here.
    """
    bits = [m["description"]]
    if m.get("when_to_use"):
        bits.append("Use when: " + "; ".join(m["when_to_use"]) + ".")
    if m.get("avoid_when"):
        bits.append("Do not use when: " + "; ".join(m["avoid_when"]) + ".")
    if m.get("outputs"):
        bits.append("Produces: " + "; ".join(m["outputs"]) + ".")
    return " ".join(bits)


def in_stage(reg, stage):
    return [m for m in enabled(reg) if m["stage"] == stage]


def narrow(reg, *, stage=None, allow=None):
    """The workflows left after filtering. May be one, or none."""
    out = []
    for m in enabled(reg):
        if stage and m["stage"] != stage:
            continue
        if allow is not None and m["id"] not in allow and m["command"] not in allow:
            continue
        out.append(m)
    return out


def candidates(reg, *, stage=None, allow=None):
    """Routing options, in the shape the router hands to Jev.

    Fewer than two is refused: a filter that leaves one option means the
    caller already made the decision, and asking anyway would dress a
    foregone conclusion up as a routing result.
    """
    out = narrow(reg, stage=stage, allow=allow)
    if len(out) < 2:
        left = [m["command"] for m in out]
        raise RegistryError(
            f"need at least two candidates after filtering "
            f"(stage={stage}, allow={allow}); "
            + (f"only {left[0]} matches, so call it directly" if left
               else "nothing matches"))
    return [{"id": m["id"], "description": describe(m)} for m in out]


def stage_candidates(reg):
    """The stages as routing options, for the first half of a two-step route.

    A stage's description has to carry what is inside it, or the first step
    is choosing between labels rather than between kinds of work.
    """
    out = []
    for name, info in sorted(reg["stages"].items(),
                             key=lambda kv: kv[1]["order"]):
        mods = in_stage(reg, name)
        if not mods:
            continue
        out.append({
            "id": name,
            "description": (info["description"] + " Workflows here: "
                            + "; ".join(f"{m['display_name']} ({m['command']})"
                                        for m in mods) + "."),
        })
    if len(out) < 2:
        raise RegistryError("need at least two populated stages to route by stage")
    return out


def next_steps(reg, workflow_id):
    """Workflows that consume this one's output. The host chains on these."""
    return [m["id"] for m in enabled(reg)
            if workflow_id in (m.get("depends_on") or [])]


# Context values that report an upstream as *not* done. A key the caller never
# mentions is unknown, not unmet: the router only reports what it was told.
NOT_DONE = {"", "no", "false", "missing", "pending", "todo", "none", "blocked"}


def blocked_by(reg, workflow_id, context=None):
    """Upstream workflows the caller explicitly reported as not done.

    Advisory. The router still routes -- whether a missing prerequisite stops
    the work is the host's call, not a decision to hide inside a filter.
    """
    m = by_id(reg, workflow_id)
    ctx = context or {}
    out = []
    for dep in (m or {}).get("depends_on") or []:
        if dep not in ctx:
            continue                                  # unknown, not unmet
        v = ctx[dep]
        if v is False or v is None or (isinstance(v, str)
                                       and v.strip().lower() in NOT_DONE):
            out.append(dep)
    return out


def interface(m, reg=None):
    """What the host program needs in order to run this workflow.

    This is the payload of a routing decision: the router names a target and
    hands back how to invoke it. It never invokes anything itself.
    """
    entry = dict(m.get("entry") or {})
    entry["system_prompt_path"] = system_prompt_path(m)
    return {
        "id": m["id"],
        "command": m["command"],
        "stage": m["stage"],
        "display_name": m["display_name"],
        "dir": m["dir"],
        "entry": entry,
        "inputs": m.get("inputs") or [],
        "outputs": m.get("outputs") or [],
        "depends_on": m.get("depends_on") or [],
        "next_steps": next_steps(reg, m["id"]) if reg else [],
        "implemented": implemented(m),
    }
