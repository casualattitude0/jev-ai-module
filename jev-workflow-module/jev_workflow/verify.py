#!/usr/bin/env python3
"""Verification suite for the workflow router.

    python3 -m jev_workflow.verify          # offline: registry, interfaces, parsing
    python3 -m jev_workflow.verify --live   # also route real requests through Jev

The offline layer must always be green. --live depends on the Jev API and on
the descriptions being good enough to separate neighbouring workflows.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time

from . import registry as R
from . import router
from .client import JevError
from .registry import RegistryError
from .router import MAX_PAYLOAD_BYTES, Route, payload_bytes, select_workflow
from .tools import TOOLS, as_openai_tools, call

PASS, FAIL = [], []

# Requests that should land on a given workflow, used by --live.
LIVE = [
    ("I'm not sure the dash should cancel attacks -- what do you think?", "brainstorm"),
    ("write down exactly how the parry window works before anyone codes it", "game-design"),
    ("level 3 has nowhere to teach the wall jump, lay it out again", "level-design"),
    ("the shopkeeper needs about twenty lines of idle dialogue", "narrative-design"),
    ("players cannot find the inventory from the pause screen", "ux-design"),
    ("the boss at level 12 kills new players too fast, retune the damage", "data-design"),
    ("nobody agrees what the game should look like; write it down", "define-art"),
    ("buttons look different on every screen, we need one set of rules", "define-ui"),
    ("decide the save file format before we start persisting runs", "tech-design"),
    ("the double jump doesn't trigger when you hold the button", "coding-game"),
    ("designers keep renaming sprites by hand, give them an importer", "coding-tools"),
    ("list every sound effect this build still needs", "asset-audit-sfx"),
    ("which character animations are still missing", "asset-audit-anim"),
    ("draw all the portraits on the checklist", "art-generate"),
    ("compose the three cues on the music list", "music-generate"),
    ("the new icons are sitting in staging, get them into the project", "apply-assets"),
    ("play the build and tell me if the new weapon feels good", "playtest"),
    ("write a regression test for the save corruption bug", "qa-test"),
    ("the game hitches every time we enter the market square", "perf-profile"),
    ("we need the game running in Japanese and Korean", "localize"),
    ("cut a release candidate for the Steam beta", "build-release"),
]


def check(name, fn):
    try:
        detail = fn()
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    except Exception as e:
        FAIL.append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}  -> {type(e).__name__}: {e}")


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _rejects(mutate, fragment):
    """Copy the workflow tree, break it, and expect load() to refuse."""
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "workflows")
    shutil.copytree(R.workflows_dir(), root)
    try:
        mutate(root)
        R.load(root)
    except RegistryError as e:
        expect(fragment.lower() in str(e).lower(), f"wrong message: {e}")
        return
    finally:
        shutil.rmtree(tmp)
    raise AssertionError(f"expected RegistryError containing {fragment!r}")


def _write(root, wid, **fields):
    path = os.path.join(root, wid, "module.json")
    with open(path) as f:
        m = json.load(f)
    m.update(fields)
    with open(path, "w") as f:
        json.dump(m, f)


def offline_backends():
    """This module asks only generic decisions; both backends speak them."""
    from .client import BACKENDS, GENERIC_PATH, _openrouter_post, resolve_backend

    def precedence():
        from . import client as _client
        saved = {k: os.environ.pop(k, None)
                 for k in ("JEV_BACKEND", "WORKFLOW_BACKEND")}
        saved_cfg = _client._CONFIG
        try:
            # With nothing set anywhere — no env, no jev.json — the built-in
            # default is native. What the committed file happens to say is a
            # separate question, checked in offline_config.
            _client._CONFIG = {}
            expect(resolve_backend() == "native", "built-in default should be native")
            os.environ["JEV_BACKEND"] = "openrouter"
            expect(resolve_backend() == "openrouter", "shared env override")
            os.environ["WORKFLOW_BACKEND"] = "native"
            expect(resolve_backend() == "native", "scoped name should win")
            expect(resolve_backend("openrouter") == "openrouter", "arg wins")
            return "arg > scoped env > shared env > built-in native"
        finally:
            _client._CONFIG = saved_cfg
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("backend precedence is arg > scoped env > shared env > built-in default",
          precedence)

    def bad_backend():
        try:
            resolve_backend("carrier-pigeon")
        except JevError as e:
            expect("backend must be one of" in str(e), f"wrong error: {e}")
            return f"rejected; {BACKENDS} accepted"
        raise AssertionError("should raise")
    check("an unknown backend is rejected", bad_backend)

    def only_generic():
        # A preset path would silently mean something else on OpenRouter, so
        # it must be refused rather than sent.
        try:
            _openrouter_post("/api/v1/decisions/model-route", {}, timeout=5,
                             retries=1)
        except JevError as e:
            expect("generic decisions endpoint" in str(e), f"wrong error: {e}")
            return f"only {GENERIC_PATH} is sent"
        raise AssertionError("should raise")
    check("a non-generic path is refused on the openrouter backend", only_generic)

    def key_reaches_jev_only():
        from .client import JEV_MODEL_PREFIXES, resolve_jev_model
        saved = {k: os.environ.pop(k, None)
                 for k in ("JEV_OPENROUTER_MODEL", "WORKFLOW_OPENROUTER_MODEL")}
        try:
            expect(resolve_jev_model().startswith(JEV_MODEL_PREFIXES),
                   "the default model is not a Jev model")
            for slug in ("openai/gpt-5.6-sol", "anthropic/claude-opus-5"):
                os.environ["JEV_OPENROUTER_MODEL"] = slug
                try:
                    resolve_jev_model()
                except JevError as e:
                    expect("not a Jev decisions model" in str(e),
                           f"{slug}: wrong error: {e}")
                    continue
                raise AssertionError(f"{slug} should be refused")
            return "Claude and GPT slugs refused"
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("the OpenRouter key cannot be pointed at a chat model",
          key_reaches_jev_only)

    def no_key():
        saved = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            _openrouter_post(GENERIC_PATH, {"state": {}, "questions": {}},
                             timeout=5, retries=1)
        except JevError as e:
            expect("OPENROUTER_API_KEY" in str(e), f"wrong error: {e}")
            return "names the missing key"
        finally:
            if saved is not None:
                os.environ["OPENROUTER_API_KEY"] = saved
        raise AssertionError("should raise")
    check("the openrouter backend without a key fails clearly", no_key)


def offline_config(module):
    """The project-root jev.json decides each module's path, and holds no keys."""
    import json as _json
    import tempfile
    import re
    from .client import CONFIG_NAME, PKG_DIR as PKG, load_config, setting_source
    from . import client as _client

    def reads_the_root_file():
        config = load_config()
        expect(config, f"no {CONFIG_NAME} found from {os.getcwd()}")
        expect(module in (config.get("modules") or {}),
               f"{CONFIG_NAME} has no entry for {module!r}")
        return f"{CONFIG_NAME} names this module"
    check(f"the project-root {CONFIG_NAME} is found and names this module",
          reads_the_root_file)

    def file_decides_when_env_is_silent():
        saved_env = {k: os.environ.pop(k, None)
                     for k in (f"{module.upper()}_BACKEND", "JEV_BACKEND")}
        saved_cfg = _client._CONFIG
        try:
            _client._CONFIG = {"backend": "native",
                               "modules": {module: {"backend": "openrouter"}}}
            value, source = setting_source("BACKEND", "native")
            expect(value == "openrouter", f"module entry ignored, got {value!r}")
            expect(f"modules.{module}" in source, f"wrong source: {source}")

            _client._CONFIG = {"backend": "openrouter"}
            value, source = setting_source("BACKEND", "native")
            expect(value == "openrouter", "top-level backend ignored")

            # Environment still wins, so a one-off run never edits a
            # committed file.
            os.environ["JEV_BACKEND"] = "native"
            value, source = setting_source("BACKEND", "native")
            expect(value == "native" and source == "$JEV_BACKEND",
                   f"env should beat the file, got {value!r} from {source}")
            return "modules.<module> > top level, and env beats both"
        finally:
            _client._CONFIG = saved_cfg
            for k, v in saved_env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("the root file decides, and the environment overrides it",
          file_decides_when_env_is_silent)

    def reserved_keys_are_not_settings():
        # jev.json's "modules" table is structure, not a setting -- and one
        # module's setting really is called MODULES, so a lookup for it must
        # not come back holding the table.
        saved = _client._CONFIG
        try:
            _client._CONFIG = {"modules": {"whatever": {"backend": "native"}}}
            value, source = setting_source("MODULES", "fallback")
            expect(value == "fallback",
                   f"the modules table was read as a setting: {value!r}")
            return "the modules table is never mistaken for a setting"
        finally:
            _client._CONFIG = saved
    check("structural keys in the config are not settings",
          reserved_keys_are_not_settings)

    def no_bare_env_reads():
        # Every setting goes through the resolver, so jev.json and the scoped
        # names work for all of them. A bare os.environ.get would silently
        # bypass both.
        import glob
        allowed = {"OPENROUTER_API_KEY"}   # a secret; the committed file refuses it
        offenders = []
        for path in glob.glob(os.path.join(PKG, "*.py")):
            if os.path.basename(path) in ("verify.py",):
                continue
            src = open(path).read()
            for name in re.findall(r'os\.environ(?:\.get)?[\(\[]"([A-Z_]+)"', src):
                if name not in allowed:
                    offenders.append(f"{os.path.basename(path)}:{name}")
        expect(not offenders, f"settings read straight from the env: {offenders}")
        return "every setting goes through the resolver"
    check("no setting bypasses the resolver", no_bare_env_reads)

    def workflows_dir_resolves():
        from .registry import workflows_source
        saved_env = {k: os.environ.pop(k, None)
                     for k in ("WORKFLOW_MODULES", "JEV_MODULES")}
        saved_cfg = _client._CONFIG
        try:
            _client._CONFIG = {}
            path, source = workflows_source()
            expect(path.endswith("workflows"), f"unexpected default: {path}")
            _client._CONFIG = {"modules": {"jev_workflow": {"modules": "/from/file"}}}
            expect(workflows_source()[0] == "/from/file", "jev.json ignored")
            # The documented name is unchanged by the rename: it is exactly
            # this module's scoped prefix plus the setting name.
            os.environ["WORKFLOW_MODULES"] = "/from/env"
            expect(workflows_source()[0] == "/from/env",
                   "WORKFLOW_MODULES stopped working")
            return "WORKFLOW_MODULES > JEV_MODULES > jev.json > shipped workflows/"
        finally:
            _client._CONFIG = saved_cfg
            for k, v in saved_env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("the workflows directory resolves like every other setting",
          workflows_dir_resolves)

    def refuses_committed_secrets():
        # jev.json is checked in, so a key in it would be committed.
        saved = _client._CONFIG
        for bad in ({"api_key": "jev_x"},
                    {"modules": {module: {"api_key": "jev_x"}}},
                    {"token": "x"}):
            d = tempfile.mkdtemp()
            path = os.path.join(d, CONFIG_NAME)
            with open(path, "w") as f:
                _json.dump(bad, f)
            cwd = os.getcwd()
            try:
                _client._CONFIG = None
                os.chdir(d)
                load_config()
            except JevError as e:
                expect("committed" in str(e) or ".env" in str(e),
                       f"wrong error: {e}")
                continue
            finally:
                os.chdir(cwd)
                _client._CONFIG = saved
            raise AssertionError(f"{bad} should be refused")
        return "a credential in the checked-in file is refused"
    check(f"{CONFIG_NAME} refuses credentials", refuses_committed_secrets)


def offline_settings(module):
    """The root .env can answer a setting per module or once for all of them."""
    from .client import MODULE, SCOPE, setting

    def scoped_wins():
        shared, scoped = "JEV_VERIFY_PROBE", f"{SCOPE}_VERIFY_PROBE"
        saved = {k: os.environ.pop(k, None) for k in (shared, scoped)}
        try:
            expect(setting("VERIFY_PROBE", "fallback") == "fallback",
                   "default should apply when neither name is set")
            os.environ[shared] = "shared"
            expect(setting("VERIFY_PROBE") == "shared", "shared name not read")
            os.environ[scoped] = "scoped"
            expect(setting("VERIFY_PROBE") == "scoped",
                   f"{scoped} should beat {shared}")
            return f"{scoped} > {shared} > default"
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("a setting resolves module-scoped name first, then the shared one",
          scoped_wins)

    def module_name():
        expect(MODULE == module, f"MODULE is {MODULE!r}, expected {module!r}")
        expect(not SCOPE.startswith("JEV_"),
               f"the scoped prefix {SCOPE}_ reads like a shared JEV_ name")
        return f"scoped prefix is {SCOPE}_"
    check("the module owns a distinct settings prefix", module_name)


def offline(reg):
    check("registry discovers every workflow directory", lambda: (
        expect(len(reg["workflows"]) >= 2, "too few"),
        f"{len(reg['workflows'])} workflows in {len(reg['stages'])} stages")[-1])

    def one_dir_each():
        for m in reg["workflows"]:
            expect(os.path.isdir(m["dir"]), f"{m['id']} has no directory")
            expect(os.path.basename(m["dir"]) == m["id"],
                   f"{m['id']} lives in {m['dir']}")
        return "every workflow owns exactly one path"
    check("each workflow is its own directory", one_dir_each)

    def unique():
        ids = [m["id"] for m in reg["workflows"]]
        cmds = [m["command"] for m in reg["workflows"]]
        expect(len(set(ids)) == len(ids), f"duplicate ids in {ids}")
        expect(len(set(cmds)) == len(cmds), f"duplicate commands in {cmds}")
        return f"{len(ids)} unique"
    check("ids and commands are unique", unique)

    for name, mutate, frag in [
        ("a manifest missing a required field",
         lambda r: _write(r, "brainstorm", description=""), "missing"),
        ("an id that disagrees with its directory",
         lambda r: _write(r, "brainstorm", id="something-else"), "directory name"),
        ("a command that disagrees with its id",
         lambda r: _write(r, "brainstorm", command="/bs"), "should be"),
        ("an unknown stage",
         lambda r: _write(r, "brainstorm", stage="vibes"), "unknown stage"),
        ("a dangling depends_on",
         lambda r: _write(r, "brainstorm", depends_on=["ghost"]), "unknown workflow"),
        ("a depends_on pointing at a later stage",
         lambda r: _write(r, "define-art", depends_on=["qa-test"]), "later stage"),
        ("an unknown entry kind",
         lambda r: _write(r, "brainstorm", entry={"kind": "telepathy"}), "entry.kind"),
        ("malformed JSON",
         lambda r: open(os.path.join(r, "brainstorm/module.json"), "w").write("{"),
         "module.json"),
    ]:
        check(f"{name} is rejected", lambda m=mutate, f=frag: _rejects(m, f))

    def too_few():
        def disable_all(root):
            for wid in os.listdir(root):
                if os.path.isdir(os.path.join(root, wid)):
                    _write(root, wid, enabled=False)
        _rejects(disable_all, "at least two")
    check("a registry with fewer than two workflows is rejected", too_few)

    def descriptions_separate():
        seen = {}
        for m in reg["workflows"]:
            d = R.describe(m)
            expect(len(d) > 80, f"{m['id']}'s description is too thin to route on")
            expect(d not in seen, f"{m['id']} and {seen.get(d)} read identically")
            seen[d] = m["id"]
        return "every workflow reads differently"
    check("descriptions are distinct and substantial", descriptions_separate)

    def boundaries():
        thin = [m["id"] for m in reg["workflows"]
                if not (m.get("when_to_use") and m.get("avoid_when"))]
        expect(not thin, f"no use/avoid boundary on {thin}")
        return "each states when to use it and when not to"
    check("every workflow draws its own boundary", boundaries)

    def deps_flow_forward():
        order = reg["stages"]
        for m in reg["workflows"]:
            for d in m.get("depends_on") or []:
                up = R.by_id(reg, d)
                expect(order[up["stage"]]["order"] <= order[m["stage"]]["order"],
                       f"{m['id']} depends on later-stage {d}")
        return "dependencies never point at a later stage"
    check("depends_on flows forward through the stages", deps_flow_forward)

    def stages_populated():
        empty = [st for st in reg["stages"] if not R.in_stage(reg, st)]
        expect(not empty, f"stages with no workflow: {empty}")
        return f"{len(reg['stages'])} stages, none empty"
    check("every declared stage holds at least one workflow", stages_populated)

    def stage_options():
        cands = R.stage_candidates(reg)
        expect(len(cands) >= 2, "too few stages to route by")
        for c in cands:
            mods = R.in_stage(reg, c["id"])
            for m in mods:
                expect(m["command"] in c["description"],
                       f"stage {c['id']} does not name {m['command']}")
                expect(R.gist(m) in c["description"],
                       f"stage {c['id']} names {m['command']} without saying "
                       "what it does")
        return f"{len(cands)} stages, each saying what is inside it"
    check("stage options carry their contents", stage_options)

    def asset_matrix():
        audits = {m["id"][len("asset-audit-"):] for m in reg["workflows"]
                  if m["id"].startswith("asset-audit-")}
        gens = {m["id"][:-len("-generate")] for m in reg["workflows"]
                if m["id"].endswith("-generate")}
        expect(audits == gens,
               f"audit and generate disagree: audit-only {audits - gens}, "
               f"generate-only {gens - audits}")
        return f"{len(audits)} asset kinds, each audited and generated"
    check("every asset kind is both audited and generated", asset_matrix)

    def next_is_inverse():
        for m in reg["workflows"]:
            for dep in m.get("depends_on") or []:
                expect(m["id"] in R.next_steps(reg, dep),
                       f"{dep} does not list {m['id']} as a next step")
        for m in reg["workflows"]:
            for nxt in R.next_steps(reg, m["id"]):
                expect(m["id"] in R.by_id(reg, nxt)["depends_on"],
                       f"{nxt} listed as next but does not depend on {m['id']}")
        return "next_steps is exactly depends_on reversed"
    check("next_steps mirrors the dependency graph", next_is_inverse)

    def blocked():
        wid = "art-generate"
        deps = R.by_id(reg, wid)["depends_on"]
        expect(R.blocked_by(reg, wid, {}) == [],
               "an unmentioned prerequisite was reported as blocking")
        expect(R.blocked_by(reg, wid, {d: "done" for d in deps}) == [],
               "a prerequisite reported done was reported as blocking")
        for bad in ("missing", "pending", "", False, None):
            expect(R.blocked_by(reg, wid, {deps[0]: bad}) == [deps[0]],
                   f"{bad!r} was not read as not-done")
        return "silent on unknown, blocking only on what the caller reported"
    check("blocked_by reports only what it was told", blocked)

    def payload_fits():
        one = payload_bytes(router._payload("x" * 400, "medium", None,
                                            R.candidates(reg), "workflow", "i"))
        cap = 32 * 1024
        expect(one < cap, f"one-shot payload is {one} bytes, over the {cap} cap")
        per = one // len(reg["workflows"])
        return (f"{one} bytes for {len(reg['workflows'])} workflows "
                f"(~{per}/workflow, budget {MAX_PAYLOAD_BYTES})")
    check("the one-shot payload stays inside the API cap", payload_fits)

    def two_step_runs():
        seen = []

        def fake_post(path, payload, **kw):
            q = list(payload["questions"])[0]
            seen.append((q, set(payload["questions"][q]["criteria"])))
            pick = "audit" if q == "stage" else "asset-audit-anim"
            return {"answers": {q: {"type": "choice", "choice": pick,
                                    "confidence": 0.9,
                                    "probabilities": {pick: 0.9}}}}

        real, router.post = router.post, fake_post
        try:
            route = select_workflow("which walk cycles are missing",
                                    two_step=True, reg=reg)
        finally:
            router.post = real

        expect([q for q, _ in seen] == ["stage", "workflow"],
               f"asked {[q for q, _ in seen]}")
        expect(seen[0][1] == set(reg["stages"]), "first question was not the stages")
        expect(seen[1][1] == {m["id"] for m in R.in_stage(reg, "audit")},
               f"second question was not narrowed to the stage: {seen[1][1]}")
        expect(route.workflow_id == "asset-audit-anim", route.workflow_id)
        expect([s["question"] for s in route.steps] == ["stage", "workflow"],
               "the route does not record both steps")
        return "stage first, then only that stage's workflows"
    check("two-step routing narrows the second question", two_step_runs)

    def single_workflow_stage():
        lonely = [st for st in reg["stages"] if len(R.in_stage(reg, st)) == 1]
        expect(lonely, "no single-workflow stage to exercise")
        st = lonely[0]
        only = R.in_stage(reg, st)[0]
        calls = []

        def fake_post(path, payload, **kw):
            calls.append(list(payload["questions"])[0])
            return {"answers": {"stage": {"type": "choice", "choice": st,
                                          "confidence": 0.88}}}

        real, router.post = router.post, fake_post
        try:
            route = select_workflow("x", two_step=True, reg=reg)
        finally:
            router.post = real
        expect(calls == ["stage"], f"asked a second question anyway: {calls}")
        expect(route.workflow_id == only["id"], route.workflow_id)
        expect(route.confidence == 0.88, "the stage confidence was lost")
        return f"{st} holds only {only['command']}, routed without a second ask"
    check("a stage holding one workflow short-circuits", single_workflow_stage)

    def auto_shape():
        calls = []

        def fake_post(path, payload, **kw):
            q = list(payload["questions"])[0]
            calls.append(q)
            pick = "discuss" if q == "stage" else "brainstorm"
            return {"answers": {q: {"type": "choice", "choice": pick,
                                    "confidence": 0.5}}}

        real, router.post = router.post, fake_post
        try:
            select_workflow("x", reg=reg)
        finally:
            router.post = real
        one = payload_bytes(router._payload("x", "medium", None,
                                            R.candidates(reg), "workflow", "i"))
        expected = "stage" if one > MAX_PAYLOAD_BYTES else "workflow"
        expect(calls[0] == expected,
               f"payload is {one} bytes but the first question was {calls[0]!r}")
        return f"{one} bytes -> asked {calls[0]!r} first"
    check("the default shape follows the payload budget", auto_shape)

    def iface():
        m = R.by_id(reg, reg["workflows"][0]["id"])
        i = R.interface(m, reg)
        for k in ("id", "command", "stage", "dir", "entry", "inputs",
                  "next_steps", "implemented"):
            expect(k in i, f"interface has no {k!r}")
        expect(json.dumps(i), "interface is not JSON-serialisable")
        return "the host gets a serialisable, executable handle"
    check("interface() is what a host program can act on", iface)

    def unimplemented_is_visible():
        for m in reg["workflows"]:
            if not R.implemented(m):
                expect(R.interface(m, reg)["implemented"] is False,
                       f"{m['id']} hides that it is an empty interface")
        done = [m["id"] for m in reg["workflows"] if R.implemented(m)]
        return f"{len(done)} of {len(reg['workflows'])} implemented"
    check("an empty interface says so", unimplemented_is_visible)

    def filters():
        one = R.candidates(reg, stage="define")
        expect({c["id"] for c in one} == {m["id"] for m in R.in_stage(reg, "define")},
               f"stage filter wrong: {[c['id'] for c in one]}")
        allow = R.candidates(reg, allow=["brainstorm", "/coding-game"])
        expect({c["id"] for c in allow} == {"brainstorm", "coding-game"},
               f"allow filter wrong: {[c['id'] for c in allow]}")
        return "stage and allow both narrow, ids or commands"
    check("candidate filters narrow the option set", filters)

    def over_narrow():
        _raises(lambda: R.candidates(reg, allow=["brainstorm"]), RegistryError,
                "at least two")
        try:
            R.candidates(reg, allow=["brainstorm"])
        except RegistryError as e:
            expect("/brainstorm" in str(e), f"does not name the survivor: {e}")
        return "refused, and names the one command to call instead"
    check("over-narrow filtering is refused, not silently routed", over_narrow)

    def parse_native():
        # The shape the live API actually returns: the value is keyed by the
        # question type, not "decision".
        data = {"answers": {"workflow": {"type": "choice",
                                         "choice": "coding-game",
                                         "confidence": 0.81,
                                         "probabilities": {"coding-game": 0.81}}}}
        ans = router._answer(data, "workflow")
        expect(ans["decision"] == "coding-game", ans)
        expect(ans["probabilities"] == {"coding-game": 0.81}, ans)
        return "a native choice answer keyed 'choice'"
    check("a native decisions response parses", parse_native)

    def parse_preset():
        ans = router._answer({"decision": "brainstorm", "confidence": 0.4},
                             "workflow")
        expect(ans["decision"] == "brainstorm", ans)
        return "preset-shaped top-level decision"
    check("a preset-shaped response parses", parse_preset)

    check("a response with no answer is refused", lambda: _raises(
        lambda: router._answer({"answers": {}}, "workflow"), ValueError,
        "no answer"))

    def off_menu_refused():
        calls = []

        def fake_post(path, payload, **kw):
            calls.append(payload)
            return {"answers": {"workflow": {"type": "choice",
                                             "choice": "ship-it",
                                             "confidence": 1.0}}}

        real, router.post = router.post, fake_post
        try:
            _raises(lambda: select_workflow("do the thing", reg=reg),
                    ValueError, "unknown workflow")
        finally:
            router.post = real
        expect(calls and "workflow" in calls[0]["questions"],
               "the request never reached a choice question")
        return "a target we never offered is not handed to the host"
    check("a decision outside the candidate list is refused", off_menu_refused)

    def payload_shape():
        seen = {}

        def fake_post(path, payload, **kw):
            seen["path"], seen["payload"] = path, payload
            return {"answers": {"workflow": {"type": "choice",
                                             "choice": "art-generate",
                                             "confidence": 0.7,
                                             "probabilities": {"art-generate": 0.7}}}}

        real, router.post = router.post, fake_post
        try:
            route = select_workflow("make the portraits", stage="generate",
                                    context={"art_bible": "written"}, reg=reg)
        finally:
            router.post = real

        q = seen["payload"]["questions"]["workflow"]
        expect(seen["path"] == "/api/v1/decisions", seen["path"])
        expect(q["type"] == "choice", q["type"])
        expect(set(q["criteria"]) == {m["id"] for m in R.in_stage(reg, "generate")},
               f"criteria not narrowed to the stage: {sorted(q['criteria'])}")
        expect(seen["payload"]["state"]["context"] == {"art_bible": "written"},
               "context was dropped")
        expect(isinstance(route, Route) and route.command == "/art-generate",
               "wrong route")
        expect(route.as_dict()["workflow"]["dir"].endswith("art-generate"),
               "route does not carry the module path")
        expect(route.as_dict()["blocked_by"] == [],
               "nothing was reported unmet, yet something is blocking")
        return "choice question, narrowed criteria, context passed through"
    check("the Jev payload is a choice over the live candidates", payload_shape)

    check("bad stakes are refused before any call", lambda: _raises(
        lambda: select_workflow("x", stakes="critical", reg=reg), ValueError,
        "stakes must be"))

    def tools_shape():
        names = {t["name"] for t in TOOLS}
        expect(names == {"select_workflow", "list_workflows"}, names)
        for t in TOOLS:
            expect(t["description"] and t["input_schema"]["type"] == "object",
                   f"{t['name']} has a malformed schema")
        expect(all(t["type"] == "function" for t in as_openai_tools()),
               "openai shape wrong")
        out = call("list_workflows", {"stage": "audit"})
        expect(len(out) == len(R.in_stage(reg, "audit")),
               f"list_workflows returned {len(out)} of "
               f"{len(R.in_stage(reg, 'audit'))}")
        expect("error" in call("no_such_tool", {}), "unknown tool not reported")
        return "two tools, both shapes, dispatch works"
    check("the function-calling surface is well formed", tools_shape)

    def standalone():
        pkg = os.path.dirname(os.path.abspath(__file__))
        for fname in os.listdir(pkg):
            if not fname.endswith(".py"):
                continue
            src = open(os.path.join(pkg, fname)).read()
            for other in ("jevrouter", "datapolicy", "requests", "httpx"):
                expect(f"import {other}" not in src,
                       f"{fname} imports {other}; modules stay standalone")
        return "no sibling-module or third-party imports"
    check("the module stays standalone", standalone)


# The API rate-limits bursts. Space the live calls out rather than let 429s
# masquerade as routing failures.
LIVE_PAUSE = 10.0


def live(reg, two_step=None):
    shape = "two-step" if two_step else ("one-shot" if two_step is False else "auto")
    print(f"\nlive routing ({shape}, real Jev calls, {LIVE_PAUSE}s apart)")
    for i, (request, expected) in enumerate(LIVE):
        if i:
            time.sleep(LIVE_PAUSE)
        def go(request=request, expected=expected):
            route = select_workflow(request, reg=reg, two_step=two_step)
            got = route.workflow_id
            expect(got == expected,
                   f"got {got} ({route.confidence}), expected {expected}")
            return f"{got}  {route.confidence}"
        check(f"{request[:52]!r}", go)


def main():
    try:
        reg = R.load()
    except RegistryError as e:
        print(f"FAIL: registry will not load: {e}", file=sys.stderr)
        return 1

    print(f"workflow router checks ({len(reg['workflows'])} workflows)")
    offline(reg)
    offline_settings("jev_workflow")
    offline_config("jev_workflow")
    offline_backends()

    if "--live" in sys.argv or "--all" in sys.argv:
        shape = (True if "--two-step" in sys.argv else
                 False if "--one-shot" in sys.argv else None)
        try:
            live(reg, two_step=shape)
        except JevError as e:
            print(f"  live layer unavailable: {e}")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for name, err in FAIL:
        print(f"  - {name}: {err}")
    return 1 if FAIL else 0


def _raises(fn, exc, fragment):
    try:
        fn()
    except exc as e:
        expect(fragment.lower() in str(e).lower(), f"wrong message: {e}")
        return "raised as expected"
    raise AssertionError(f"expected {exc.__name__}")


if __name__ == "__main__":
    sys.exit(main())
