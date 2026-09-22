#!/usr/bin/env python3
"""Verification suite for the Jev model router.

    python3 verify.py                 # offline checks, no API calls
    python3 verify.py --models        # every model and every effort variant
    python3 verify.py --models --no-smoke   # ...without the live CLI calls
    python3 verify.py --live          # live Jev routing checks
    python3 verify.py --all           # everything

Exits 0 only if every check passes. JEV_TEST_GAP sets the pause between live
Jev calls (default 45s; the API rate-limits bursts).
"""
import argparse
import copy
import json
import os
import sys
import tempfile
import time

from . import (DispatchError, JevError, Selection, as_anthropic_tools,
                       as_openai_tools, call, load_registry, select_model, serve)
from .client import post
from .router import ASSESS_QUESTIONS
from .registry import (RegistryError, candidates, enabled, load,
                                split_variant, variants)

PASS, FAIL, SKIP = [], [], []
GAP = int(os.environ.get("JEV_TEST_GAP", "45"))


class SkipCheck(Exception):
    pass


def check(name, fn):
    try:
        detail = fn()
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    except SkipCheck as e:
        SKIP.append((name, str(e)))
        print(f"  SKIP  {name}  -> {e}")
    except Exception as e:
        FAIL.append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}  -> {type(e).__name__}: {e}")


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _rejects(obj, expect_err):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(obj, f)
        path = f.name
    try:
        load(path)
    except RegistryError as e:
        expect(expect_err.lower() in str(e).lower(), f"wrong message: {e}")
        return
    finally:
        os.unlink(path)
    raise AssertionError(f"expected RegistryError containing {expect_err!r}")


# ------------------------------------------------------------- offline

def offline(reg):
    print("offline checks")
    vs = variants(reg)
    cands = candidates(reg)
    by_id = {c["id"]: c for c in cands}

    check("registry loads and validates",
          lambda: f"{len(enabled(reg))} models, {len(vs)} variants")

    def expansion():
        want = sum(len(m["efforts"]) for m in enabled(reg))
        expect(len(vs) == want, f"expected {want} variants, got {len(vs)}")
        return f"{want} = sum of per-model efforts"
    check("every model x effort pair becomes one option", expansion)

    def unique():
        ids = [v["id"] for v in vs]
        expect(len(set(ids)) == len(ids), "duplicate variant ids")
        for v in vs:
            base, effort = split_variant(v["id"])
            expect(base == v["model"]["id"] and effort == v["effort"],
                   f"variant id {v['id']} does not round-trip")
        return "ids unique and round-trip"
    check("variant ids are unique and parseable", unique)

    for bad, err, why in [
        ("dup", "duplicate", "duplicate model id"),
        ("missing", "missing", "missing required field"),
        ("tier", "unknown tier", "unknown tier"),
        ("cost", "bad base_cost", "bad base_cost"),
        ("effort", "unknown effort", "unknown effort"),
        ("sep", "must not contain", "model id containing '@'"),
    ]:
        def mk(bad=bad, err=err):
            b = copy.deepcopy(reg)
            if bad == "dup":
                b["models"].append(copy.deepcopy(b["models"][0]))
            elif bad == "missing":
                del b["models"][0]["provider"]
            elif bad == "tier":
                b["models"][0]["tier"] = "turbo"
            elif bad == "cost":
                b["models"][0]["base_cost"] = "cheap"
            elif bad == "effort":
                b["models"][0]["efforts"] = ["ludicrous"]
            elif bad == "sep":
                b["models"][0]["id"] = "some@model"
            _rejects(b, err)
        check(f"{why} rejected", mk)

    def shape():
        for c in cands:
            expect(set(c) == {"id", "description", "cost", "latency"},
                   f"unexpected keys {sorted(c)}")
            expect(c["cost"] in ("low", "medium", "high"), f"bad cost {c['cost']}")
            expect(c["latency"] in ("low", "medium", "high"), "bad latency")
        return f"{len(cands)} candidates, Jev-shaped"
    check("candidates match the Jev model-route contract", shape)

    def effort_in_desc():
        for v in vs:
            d = by_id[v["id"]]["description"]
            expect(f"at {v['effort']} effort" in d,
                   f"{v['id']} description omits its effort")
        return "every description names its effort"
    check("effort is visible to Jev in every description", effort_in_desc)

    def context_in_desc():
        for v in vs:
            d = by_id[v["id"]]["description"]
            ctx = v["model"].get("context_tokens")
            want = f"Context {ctx:,} tokens." if ctx else "Context window unspecified."
            expect(want in d, f"{v['id']} description omits context: {d[:80]}")
        return "context window reaches Jev"
    check("context window is visible to Jev in every description", context_in_desc)

    def avoid_folded():
        for v in vs:
            if v["model"].get("avoid_when"):
                expect("Avoid when:" in by_id[v["id"]]["description"],
                       f"{v['id']} avoid_when not folded in")
        return "avoid_when reaches Jev"
    check("avoid_when is folded into the description", avoid_folded)

    def effort_costs():
        order = {"low": 0, "medium": 1, "high": 2}
        table = reg["efforts"]
        for m in enabled(reg):
            mine = [v for v in vs if v["model"]["id"] == m["id"]]
            mine.sort(key=lambda v: table[v["effort"]]["step"])
            for a, b in zip(mine, mine[1:]):
                expect(order[a["cost"]] <= order[b["cost"]],
                       f"{a['id']} costs more than {b['id']}")
                expect(order[a["latency"]] <= order[b["latency"]],
                       f"{a['id']} slower than {b['id']}")
        return "cost and latency rise with effort"
    check("higher effort never costs less than lower effort", effort_costs)

    def allow_variant():
        ids = [vs[0]["id"], vs[-1]["id"]]
        got = [c["id"] for c in candidates(reg, allow=ids)]
        expect(got == ids, f"expected {ids}, got {got}")
        return f"exact variants: {ids}"
    check("allow accepts variant ids", allow_variant)

    def allow_model():
        m = enabled(reg)[0]
        got = [c["id"] for c in candidates(reg, allow=[m["id"], enabled(reg)[1]["id"]])]
        want = len(m["efforts"]) + len(enabled(reg)[1]["efforts"])
        expect(len(got) == want, f"expected {want} variants, got {len(got)}")
        return f"bare model id expands to {want} variants"
    check("allow accepts bare model ids and keeps all their efforts", allow_model)

    def effort_filter():
        got = candidates(reg, efforts=["low"])
        expect(all(c["id"].endswith("@low") for c in got), "leaked a non-low effort")
        return f"{len(got)} low-effort variants"
    check("efforts filter restricts effort levels", effort_filter)

    def context_filter():
        known = sorted({m["context_tokens"] for m in enabled(reg)
                        if m.get("context_tokens")})
        expect(len(known) >= 2, "need two distinct context windows to test the filter")
        # A threshold above the smallest window but at or below the largest.
        threshold = known[0] + 1
        got = candidates(reg, min_context_tokens=threshold)
        dropped = []
        for m in enabled(reg):
            ctx = m.get("context_tokens")
            survived = any(split_variant(c["id"])[0] == m["id"] for c in got)
            if ctx is not None and ctx < threshold:
                expect(not survived,
                       f"{m['id']} has {ctx} < {threshold} but survived")
                dropped.append(m["id"])
            else:
                # An unspecified window is unknown, not known-too-small: kept.
                expect(survived, f"{m['id']} ({ctx}) should have survived")
        expect(dropped, "the filter dropped nothing; it is not being applied")
        return f"dropped {dropped} at >={threshold:,}"
    check("min_context_tokens drops models with a known smaller window", context_filter)

    def narrow():
        try:
            candidates(reg, allow=[vs[0]["id"]])
        except RegistryError:
            return "raised as expected"
        raise AssertionError("single-candidate filter should raise")
    check("a filter leaving one option is rejected", narrow)

    def schemas():
        a, o = as_anthropic_tools(), as_openai_tools()
        expect([t["name"] for t in a] == ["select_model", "route_and_serve"], "names")
        props = a[0]["input_schema"]["properties"]
        for k in ("efforts", "min_context_tokens", "allow", "stakes"):
            expect(k in props, f"select_model schema missing {k}")
        json.dumps(a); json.dumps(o)
        return "both shapes, effort and context exposed"
    check("tool schemas valid and expose effort + context", schemas)

    check("unknown tool call returns an error dict",
          lambda: expect("error" in call("nope", {}), "no error key"))
    check("handler exceptions are caught by call()",
          lambda: expect("error" in call("select_model", {"task": "x", "stakes": "?"}),
                         "exception escaped"))

    def stakes_guard():
        try:
            select_model("x", stakes="critical")
        except ValueError:
            return "raised before any HTTP call"
        raise AssertionError("bad stakes should raise")
    check("invalid stakes rejected before hitting the API", stakes_guard)

    def body_cap():
        try:
            post("/api/v1/decisions",
             {"state": {"task": "x" * 40000}, "questions": ASSESS_QUESTIONS})
        except JevError as e:
            expect("32 KiB" in str(e), f"wrong error: {e}")
            return "rejected locally, no request sent"
        raise AssertionError("oversized body should raise")
    check("oversized payload rejected before sending", body_cap)

    offline_floor(reg)
    offline_dispatch(reg)


def offline_floor(reg):
    """The capability floor: difficulty decides, cost never participates."""
    from .registry import floor_for, meets, qualified

    def rounding():
        cases = {0.4: "0", 2.1: "2", 2.49: "2", 2.5: "3", 2.73: "3", 3.0: "3"}
        for diff, want_level in cases.items():
            rule = floor_for(reg, "code", diff)
            want = reg["thresholds"]["code"][want_level]
            expect(rule.get("min_tier") == want.get("min_tier"),
                   f"difficulty {diff} took the wrong level: {rule}")
        return "2.49 stays level 2, 2.5 becomes level 3"
    check("difficulty rounds to the nearest floor, not upward", rounding)

    def tier_gate():
        keep, cut, rule = qualified(reg, "code", 3.0)
        expect(rule["min_tier"] == "deep", f"unexpected floor {rule}")
        for v in keep:
            expect(v["model"]["tier"] == "deep", f"{v['id']} is not deep tier")
        expect(cut, "nothing was excluded at the frontier floor")
        return f"kept {sorted({v['model']['id'] for v in keep})}"
    check("min_tier excludes models below the floor", tier_gate)

    def unknown_metric_passes():
        # No code benchmark spans both model families, so a model that does not
        # publish the gated metric must not be treated as failing it.
        rule = {"require": {"swe_bench_pro": 70}}
        m = {"tier": "deep", "benchmarks": {"browsecomp": 92.2}}
        ok, why = meets({"model": m, "effort": "high"}, rule, reg["efforts"])
        expect(ok, f"unpublished metric treated as failure: {why}")
        return "absence is unknown, not failure"
    check("a model that does not publish the gated metric is kept", unknown_metric_passes)

    def known_metric_fails():
        rule = {"require": {"swe_bench_pro": 70}}
        m = {"tier": "deep", "benchmarks": {"swe_bench_pro": 64.6}}
        ok, why = meets({"model": m, "effort": "high"}, rule, reg["efforts"])
        expect(not ok, "a published score below the floor should fail")
        expect("64.6" in why and "70" in why, f"reason is not specific: {why}")
        return why
    check("a published score below the floor is excluded", known_metric_fails)

    def require_published():
        # Browser work has a specialist benchmark; a model that never reported
        # one is not a browser model, so here silence is evidence of absence.
        keep, cut, rule = qualified(reg, "browser", 3.0)
        expect("browsecomp" in (rule.get("require_published") or []),
               f"browser floor is not strict: {rule}")
        survivors = {v["model"]["id"] for v in keep}
        for m in enabled(reg):
            publishes = (m.get("benchmarks") or {}).get("browsecomp") is not None
            if not publishes:
                expect(m["id"] not in survivors,
                       f"{m['id']} survived without publishing browsecomp")
        expect(any("does not publish" in w for w in cut.values()),
               f"no model was cut for silence: {cut}")
        return f"kept only {sorted(survivors)}"
    check("a specialist kind requires the metric to be published", require_published)

    def reason_quality():
        # A capability shortfall is more useful than an effort shortfall, so it
        # must be the reason reported when both apply.
        _, cut, _ = qualified(reg, "browser", 3.0)
        fast = [m["id"] for m in enabled(reg) if m["tier"] == "fast"]
        for mid in fast:
            expect(cut.get(mid, "").startswith("tier "),
                   f"{mid} was explained as {cut.get(mid)!r}, not its tier")
        return "capability shortfall outranks effort shortfall"
    check("exclusion reasons name the capability gap, not the effort gap",
          reason_quality)

    def effort_gate():
        keep, _, rule = qualified(reg, "code", 3.0)
        expect(rule.get("min_effort") == "high", f"no effort floor: {rule}")
        table = reg["efforts"]
        want = table["high"]["step"]
        for v in keep:
            expect(table[v["effort"]]["step"] >= want,
                   f"{v['id']} is below the effort floor")
        return "a frontier task cannot be served at a glance"
    check("difficulty raises the effort floor too", effort_gate)

    def long_context_gate():
        over = reg["thresholds"]["_long_context"]["over_tokens"] + 1
        _, cut, rule = qualified(reg, "code", 0.0, input_tokens=over)
        expect("mrcr_long_context_recall" in (rule.get("require") or {}),
               f"long-context gate did not fire: {rule}")
        expect("gpt-5.6-luna" in cut, f"Luna survived a long-context task: {cut}")
        return f"Luna cut: {cut['gpt-5.6-luna']}"
    check("a large input gates on usable context, not window size", long_context_gate)

    def cost_absent():
        # The floor must be blind to price; only stage two may weigh it.
        for kind in ("code", "browser", "research", "writing", "general"):
            for level in ("0", "1", "2", "3"):
                rule = reg["thresholds"][kind][level]
                for key in rule:
                    expect(key in ("min_tier", "min_effort", "require",
                                   "require_published"),
                           f"{kind}/{level} floor mentions {key!r}")
                metrics = list(rule.get("require") or {}) + \
                          list(rule.get("require_published") or [])
                for metric in metrics:
                    expect("price" not in metric and "cost" not in metric,
                           f"{kind}/{level} gates on {metric!r}")
        return "no floor mentions price or cost"
    check("the capability floor never considers cost", cost_absent)

    def priorities_order():
        pr = reg.get("default_priorities") or []
        expect(pr and pr[-1] == "cost",
               f"cost should rank last in default_priorities, got {pr}")
        return f"cost ranks last: {pr}"
    check("cost ranks last among default priorities", priorities_order)

    def priorities_follow_the_floor():
        # The tie-break tracks what the floor already decided. Where the floor
        # admits everything, it has called the task routine, and a
        # quality-first ordering would just buy the dearest qualifying model.
        from .registry import priorities_for
        whole = len(variants(reg))
        for difficulty in (0.0, 1.0):
            keep, _, _ = qualified(reg, "general", difficulty)
            expect(len(keep) == whole,
                   f"difficulty {difficulty} no longer admits every variant")
            pr = priorities_for(reg, difficulty)
            expect(pr[0] == "cost",
                   f"difficulty {difficulty} should lead on cost, got {pr}")
        for difficulty in (2.0, 3.0):
            pr = priorities_for(reg, difficulty)
            expect(pr == reg["default_priorities"],
                   f"difficulty {difficulty} should keep the default, got {pr}")
            expect(pr[-1] == "cost", f"cost should rank last, got {pr}")
        return "cost leads only where the floor admits everything"
    check("the tie-break follows the floor's own verdict",
          priorities_follow_the_floor)

    def explicit_priorities_win():
        # A caller who names an ordering must get it, whatever the difficulty.
        sent = {}
        import jev_model_router.router as _r
        real = _r.post

        def spy(path, payload, **kw):
            sent.update(payload)
            return {"decision": "claude-opus-5@high", "confidence": 1.0}
        _r.post = spy
        try:
            _r.select_model("x", kind="code", difficulty=0.0,
                            priorities=["quality", "cost"])
            expect(sent["priorities"] == ["quality", "cost"],
                   f"explicit priorities were overridden: {sent['priorities']}")
            sent.clear()
            _r.select_model("x", kind="code", difficulty=0.0)
            expect(sent["priorities"][0] == "cost",
                   f"derived priorities not applied: {sent['priorities']}")
        finally:
            _r.post = real
        return "an explicit ordering is never second-guessed"
    check("explicit priorities beat the derived ones", explicit_priorities_win)

    def input_tokens_implies_context():
        big = max(m["context_tokens"] for m in enabled(reg)) + 1
        try:
            select_model("x", kind="code", difficulty=0.0, input_tokens=big)
        except (ValueError, RegistryError) as e:
            expect("floor" in str(e).lower() or "candidates" in str(e).lower(),
                   f"unexpected error: {e}")
            return "an input larger than every window leaves nothing"
        raise AssertionError("should not route an input no model can hold")
    check("input_tokens implies a context requirement", input_tokens_implies_context)

    def single_survivor_short_circuits():
        # Exactly one qualifying variant is already the answer; a second Jev
        # call would add nothing, so it must not be made.
        saved = os.environ.pop("JEV_API_KEY", None)
        try:
            sel = select_model("x", kind="browser", difficulty=3.0,
                               allow=["gpt-5.6-sol@max"], efforts=["max"])
            expect(sel.variant_id == "gpt-5.6-sol@max", f"got {sel.variant_id}")
            expect(sel.confidence == 1.0, "a sole survivor should be certain")
            return "answered with no second call, key not even needed"
        finally:
            if saved is not None:
                os.environ["JEV_API_KEY"] = saved
    check("a single qualifying variant skips the second call",
          single_survivor_short_circuits)

    def no_survivor_raises():
        try:
            select_model("x", kind="code", difficulty=3.0, allow=["gpt-5.6-luna"])
        except (ValueError, RegistryError) as e:
            expect("floor" in str(e).lower() or "candidates" in str(e).lower(),
                   f"unexpected error: {e}")
            return "refuses rather than dropping the floor"
        raise AssertionError("should raise when nothing qualifies")
    check("nothing clearing the floor is an error, not a downgrade",
          no_survivor_raises)


def _sel(model, effort="high", vid="x@high"):
    return Selection(variant_id=vid, model_id=model.get("id", "x"), effort=effort,
                     confidence=1.0, model=model)


def offline_backends(reg):
    """The native and openrouter backends must answer the same calls."""
    import re
    from .client import (BACKENDS, TRANSLATORS, _model_route_request,
                                 _model_route_response, resolve_backend)

    def precedence():
        from . import client as _client
        saved = {k: os.environ.pop(k, None)
                 for k in ("JEV_BACKEND", "MODEL_ROUTER_BACKEND")}
        saved_cfg = _client._CONFIG
        try:
            # With nothing set anywhere — no env, no jev.json — the built-in
            # default is native. What the committed file happens to say is a
            # separate question, checked in offline_config.
            _client._CONFIG = {}
            expect(resolve_backend() == "native", "built-in default should be native")
            os.environ["JEV_BACKEND"] = "openrouter"
            expect(resolve_backend() == "openrouter", "shared env override")
            os.environ["MODEL_ROUTER_BACKEND"] = "native"
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

    def every_path_translatable():
        # Every endpoint this module posts to needs a translation, or the
        # openrouter backend is silently half-working.
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "router.py")).read()
        paths = set(re.findall(r'post\(\s*"([^"]+)"', src))
        expect(paths, "found no post() paths in router.py")
        missing = sorted(paths - set(TRANSLATORS))
        expect(not missing, f"no openrouter translation for {missing}")
        return f"all {len(paths)} endpoints translate"
    check("every endpoint the router calls has an openrouter translation",
          every_path_translatable)

    def candidates_survive_translation():
        cands = candidates(reg)
        state, questions = _model_route_request({
            "task": "x", "stakes": "low", "priorities": ["cost"],
            "candidates": cands})
        q = questions["decision"]
        expect(q["type"] == "choice", "model-route must become a choice")
        expect(set(q["criteria"]) == {c["id"] for c in cands},
               "a candidate was lost in translation")
        for c in cands:
            text = q["criteria"][c["id"]]
            expect(c["description"] in text, f"{c['id']}: description dropped")
            # cost and latency are separate fields natively; the generic
            # endpoint takes one string, so they must be folded in, not lost.
            for k in ("cost", "latency"):
                expect(c[k] in text, f"{c['id']}: {k} dropped in translation")
        expect(state["task"] == "x" and state["stakes"] == "low",
               "state lost the task or stakes")
        return f"{len(cands)} candidates keep id, description, cost, latency"
    check("model-route translation keeps every candidate and its facts",
          candidates_survive_translation)

    def preset_rebuilds_the_flat_keys():
        # The native preset answers with flattened keys alongside its answers;
        # the openrouter path has to rebuild them from the generic response.
        out = _model_route_response(
            {"decision": {"choice": "claude-opus-5@high", "confidence": 0.81,
                          "probabilities": {"claude-opus-5@high": 0.81}}}, {})
        expect(out["decision"] == "claude-opus-5@high",
               "model-route lost its decision")
        expect(out["confidence"] == 0.81 and out["probabilities"],
               "confidence or probabilities not rebuilt")
        expect(out["guidance"] == "" and
               out["guidance_source"] == "unavailable_on_openrouter",
               "guidance must be empty and say why, not be invented")
        return "decision, confidence and probabilities rebuilt; guidance honest"
    check("the model-route translation rebuilds the flat keys",
          preset_rebuilds_the_flat_keys)

    def key_reaches_jev_only():
        from .client import JEV_MODEL_PREFIXES, resolve_jev_model
        saved = {k: os.environ.pop(k, None)
                 for k in ("JEV_OPENROUTER_MODEL",
                           "MODEL_ROUTER_OPENROUTER_MODEL")}
        try:
            expect(resolve_jev_model().startswith(JEV_MODEL_PREFIXES),
                   "the default model is not a Jev model")
            # The one configurable knob must not become a way to spend this key
            # on a chat model.
            for slug in ("openai/gpt-5.6-sol", "anthropic/claude-opus-5",
                         "anthropic/claude-haiku-4.5"):
                os.environ["JEV_OPENROUTER_MODEL"] = slug
                try:
                    resolve_jev_model()
                except JevError as e:
                    expect("not a Jev decisions model" in str(e),
                           f"{slug}: wrong error: {e}")
                    continue
                raise AssertionError(f"{slug} should be refused")
            # OpenRouter serves a pinned version without the "~", which marks
            # a floating alias; refusing that spelling would refuse every pin.
            for pin in ("typesafe/jev-1.13-20260917", "~typesafe/jev-latest"):
                os.environ["JEV_OPENROUTER_MODEL"] = pin
                expect(resolve_jev_model() == pin,
                       f"a Jev slug should be allowed: {pin}")
            # Still inside the Jev family only: another model from the same
            # vendor is not a decisions model and must not have the key.
            os.environ["JEV_OPENROUTER_MODEL"] = "typesafe/chat-9"
            try:
                resolve_jev_model()
            except JevError:
                pass
            else:
                raise AssertionError("typesafe/chat-9 should be refused")
            return "Claude and GPT slugs refused; both Jev spellings allowed"
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
            from .client import _openrouter_post
            _openrouter_post("/api/v1/decisions", {"state": {}, "questions": {}},
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

    def registry_path_resolves():
        from .registry import registry_path
        saved_env = {k: os.environ.pop(k, None)
                     for k in ("MODEL_ROUTER_MODELS", "JEV_MODELS")}
        saved_cfg = _client._CONFIG
        try:
            _client._CONFIG = {}
            path, source = registry_path()
            expect(path.endswith("models.json"), f"unexpected default: {path}")
            _client._CONFIG = {"modules": {"jev_model_router":
                                           {"models": "/from/file.json"}}}
            expect(registry_path()[0] == "/from/file.json", "jev.json ignored")
            os.environ["JEV_MODELS"] = "/from/env.json"
            expect(registry_path()[0] == "/from/env.json", "env should win")
            expect(registry_path("/explicit.json")[0] == "/explicit.json",
                   "an explicit path should win over everything")
            return "argument > env > jev.json > shipped models.json"
        finally:
            _client._CONFIG = saved_cfg
            for k, v in saved_env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("the registry file resolves like every other setting",
          registry_path_resolves)

    def timeout_resolves():
        from .dispatch import DEFAULT_CLI_TIMEOUT, cli_timeout
        saved_env = {k: os.environ.pop(k, None)
                     for k in ("MODEL_ROUTER_CLI_TIMEOUT", "JEV_CLI_TIMEOUT")}
        saved_cfg = _client._CONFIG
        try:
            _client._CONFIG = {}
            expect(cli_timeout() == DEFAULT_CLI_TIMEOUT, "default timeout changed")
            _client._CONFIG = {"cli_timeout": 42}
            expect(cli_timeout() == 42, "jev.json ignored for the timeout")
            os.environ["JEV_CLI_TIMEOUT"] = "77"
            expect(cli_timeout() == 77, "env should win")
            os.environ["JEV_CLI_TIMEOUT"] = "soon"
            try:
                cli_timeout()
            except DispatchError as e:
                expect("whole number" in str(e), f"wrong error: {e}")
            else:
                raise AssertionError("a non-numeric timeout should be refused")
            return "argument-free resolution, and a bad value is refused"
        finally:
            _client._CONFIG = saved_cfg
            for k, v in saved_env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("the CLI timeout resolves like every other setting", timeout_resolves)

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


def offline_dispatch(reg):
    from .dispatch import DEFAULT_CLI, resolve_transport

    def unknown_provider():
        # openrouter needs no per-provider adapter, so an unknown provider is
        # only ever a slug that OpenRouter will reject; the cli transport is
        # the one that must know the provider up front.
        try:
            serve(_sel({"provider": "acme", "id": "acme-1"}), "hi", transport="cli")
        except DispatchError as e:
            expect("no CLI configured" in str(e), f"wrong error: {e}")
            return "names the gap"
        raise AssertionError("should raise")
    check("unknown provider fails clearly on the cli transport", unknown_provider)

    def precedence():
        saved = os.environ.pop("JEV_DISPATCH", None)
        try:
            expect(resolve_transport({}) == "cli", "default should be cli")
            expect(resolve_transport({"transport": "cli"}) == "cli",
                   "model override")
            expect(resolve_transport({"transport": "cli"}, "cli") == "cli",
                   "arg wins")
            os.environ["JEV_DISPATCH"] = "cli"
            expect(resolve_transport({}) == "cli", "env override")
            return "arg > env > model > cli"
        finally:
            os.environ.pop("JEV_DISPATCH", None)
            if saved is not None:
                os.environ["JEV_DISPATCH"] = saved
    check("transport precedence is arg > env > model > cli default", precedence)

    def bad_transport():
        # "api" and "openrouter" are included on purpose: both HTTP paths to a
        # model were removed, and a caller still asking for one must be told
        # so rather than quietly falling back to the cli.
        for name in ("pigeon", "api", "openrouter"):
            try:
                serve(_sel({"provider": "anthropic"}), "hi", transport=name)
            except DispatchError as e:
                expect("transport must be one of" in str(e),
                       f"{name}: wrong error: {e}")
                continue
            raise AssertionError(f"{name}: should raise")
        return "unknown and removed transports both rejected"
    check("unknown transport rejected", bad_transport)

    def key_on_cli():
        try:
            serve(_sel({"provider": "anthropic"}), "hi", transport="cli",
                  api_key="sk-test")
        except DispatchError as e:
            expect("no transport here takes an api_key" in str(e),
                   f"wrong error: {e}")
            return "api_key not silently ignored"
        raise AssertionError("should raise")
    check("api_key passed to serve() is rejected outright", key_on_cli)

    def effort_flags():
        for provider, cfg in DEFAULT_CLI.items():
            tmpl = cfg.get("effort_args")
            expect(tmpl, f"{provider} has no effort_args")
            argv = [a.format(effort="high") for a in tmpl]
            expect("high" in " ".join(argv), f"{provider} drops the effort value")
            # Each template must be one argv element; a joined "-c k=v" would
            # reach the CLI as a single unparseable argument.
            for a in argv:
                expect(" " not in a.strip() or "=" in a,
                       f"{provider} builds a malformed argv element: {a!r}")
        return "argv templates well-formed"
    check("effort reaches the cli transport", effort_flags)

    def codex_argv():
        cfg = DEFAULT_CLI["openai"]
        argv = [a.format(effort="high") for a in cfg["effort_args"]]
        expect(argv == ["-c", "model_reasoning_effort=high"],
               f"codex effort argv is {argv}; -c and its value must be separate")
        return "-c and key=value are separate argv elements"
    check("codex effort flag splits into two argv elements", codex_argv)

    def every_model_has_a_cli():
        from .dispatch import _cli_config
        for m in enabled(reg):
            cfg = _cli_config(m)
            expect(cfg.get("command"), f"{m['id']}: CLI block names no command")
        return f"all {len(enabled(reg))} models name a CLI"
    check("every model is reachable by some CLI", every_model_has_a_cli)

    def no_key_path():
        # The guarantee: the serving path never reads a key, and has no HTTP
        # client to spend one with. The key names do appear in dispatch.py, but
        # only in STRIPPED_KEY_ENV, which exists to take them away.
        import re
        from .dispatch import STRIPPED_KEY_ENV
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "dispatch.py")).read()
        for name in ("api_key_for", "urlopen", "urllib"):
            expect(name not in src, f"dispatch.py still references {name}")
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
            expect(key in STRIPPED_KEY_ENV, f"{key} is not stripped from the CLI env")
            for read in (f'environ.get("{key}")', f'environ["{key}"]',
                         f"environ.get('{key}')", f"environ['{key}']"):
                expect(read not in src, f"dispatch.py reads {key}")
        # Nothing may be read from the environment as a credential at all.
        reads = set(re.findall(r'environ(?:\.get)?[\(\[]"([A-Z_]+)"', src))
        creds = {r for r in reads if "KEY" in r or "TOKEN" in r or "SECRET" in r}
        expect(not creds, f"dispatch.py reads credentials from the env: {creds}")
        return "reads no key, and has no HTTP client to spend one with"
    check("the serving path reads no API key at all", no_key_path)

    def cli_gets_no_keys():
        from .dispatch import STRIPPED_KEY_ENV, cli_env
        saved = {k: os.environ.get(k) for k in STRIPPED_KEY_ENV}
        try:
            for k in STRIPPED_KEY_ENV:
                os.environ[k] = "sk-should-not-reach-the-cli"
            env = cli_env()
            for k in STRIPPED_KEY_ENV:
                expect(k not in env, f"{k} reaches the model CLI")
            # Nothing credential-shaped should survive, whatever its name.
            left = [k for k in env if k.endswith("_API_KEY")]
            expect(not left, f"credentials still reach the model CLI: {left}")
            # Only the keys go; the CLI still needs the rest of the environment
            # to find its own binary and its own logged-in session.
            expect(env.get("PATH") == os.environ.get("PATH"),
                   "PATH was dropped along with the keys")
            return f"{len(STRIPPED_KEY_ENV)} keys stripped, the rest kept"
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("no API key reaches the model CLI's environment", cli_gets_no_keys)

    def cli_call_passes_the_filtered_env():
        # The filter is worthless if serve() forgets to pass it.
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "dispatch.py")).read()
        run = src[src.index("proc = subprocess.run("):]
        expect("env=cli_env()" in run[:300],
               "subprocess.run does not pass the filtered environment")
        return "subprocess.run(env=cli_env())"
    check("the cli call actually uses the stripped environment",
          cli_call_passes_the_filtered_env)

    def binary_resolution():
        from .dispatch import resolve_command
        for provider, cfg in DEFAULT_CLI.items():
            found = resolve_command(cfg)
            if found is None:
                continue
            expect(os.access(found, os.X_OK), f"{provider}: {found} not executable")
        return "resolves PATH then fallback paths"
    check("CLI binaries resolve via PATH or fallback paths", binary_resolution)

# ---------------------------------------------------- per-model coverage

SMOKE = "Reply with exactly one word: pong"


def per_model(reg, smoke=True):
    from .dispatch import cli_available
    vs = variants(reg)
    models = enabled(reg)
    print(f"\nper-model checks ({len(models)} models, {len(vs)} variants)")

    for m in models:
        mine = [v for v in vs if v["model"]["id"] == m["id"]]
        print(f"\n  [{m['display_name']}]  {m['id']}  "
              f"({len(mine)} efforts: {', '.join(v['effort'] for v in mine)})")

        check(f"{m['id']}: all declared efforts expand to options",
              lambda m=m, mine=mine: expect(
                  {v["effort"] for v in mine} == set(m["efforts"]),
                  f"expected {m['efforts']}, got {[v['effort'] for v in mine]}")
              or f"{len(mine)} options")

        avail = cli_available(m)

        def cli_check(m=m, avail=avail):
            if not avail:
                raise SkipCheck(f"CLI for provider {m['provider']!r} not on PATH")
            return "CLI on PATH"
        check(f"{m['id']}: dispatch CLI available", cli_check)

        if not (smoke and avail):
            continue

        for v in mine:
            def smoke_call(v=v):
                sel = Selection(variant_id=v["id"], model_id=v["model"]["id"],
                                effort=v["effort"], confidence=1.0, model=v["model"])
                reply = serve(sel, SMOKE, transport="cli")
                expect(reply.strip(), "empty reply")
                return f"{reply.strip()[:30]!r}"
            check(f"{v['id']}: live CLI call succeeds", smoke_call)


# A probe task per (kind, tier). The kind matters: the code floor gates on
# SWE-bench Pro, so a model that leads on browser work and not on code is
# unreachable for a hard *code* task by design, and probing it with one would
# be testing the floor rather than the model's reachability.
PROBE_TASKS = {
    "code": {
        "fast": "change a log message string in one file",
        "balanced": "add pagination to an existing REST endpoint and its tests",
        "deep": "redesign the transaction ledger across the service with "
                "ambiguous requirements and no downtime",
    },
    "browser": {
        "fast": "open a page and read back its title",
        "balanced": "drive a web checkout and verify the order total",
        "deep": "navigate an unfamiliar admin console end to end and reconcile "
                "every billing discrepancy it reports",
    },
}
PROBE_DIFFICULTY = {"fast": 0.0, "balanced": 2.0, "deep": 3.0}
PROBE_STAKES = {"fast": "low", "balanced": "medium", "deep": "high"}


def _probe_kind(reg, model, difficulty):
    """A kind whose floor this model clears, so the probe is a fair one."""
    from .registry import qualified
    for kind in PROBE_TASKS:
        keep, _, _ = qualified(reg, kind, difficulty, allow=[model["id"]])
        if keep:
            return kind
    return None


def per_model_routing(reg):
    from .registry import qualified
    models = enabled(reg)
    tiers = {m["id"]: m["tier"] for m in models}

    print(f"\nper-model routing reachability ({len(models)} models, {GAP}s apart)")
    for i, m in enumerate(models):
        difficulty = PROBE_DIFFICULTY[m["tier"]]
        kind = _probe_kind(reg, m, difficulty)

        def reach(m=m, kind=kind, difficulty=difficulty):
            if kind is None:
                raise SkipCheck(
                    f"no kind's floor at difficulty {difficulty} admits "
                    f"{m['id']}; it is unreachable by design, not by accident")
            # The foil has to clear the same floor, or it is excluded before
            # Jev ever sees it and the sole survivor wins without choosing.
            keep, _, _ = qualified(reg, kind, difficulty)
            foil = next((v["model"]["id"] for v in keep
                         if v["model"]["id"] != m["id"]
                         and v["model"]["tier"] != m["tier"]), None)
            if foil is None:
                raise SkipCheck(
                    f"no other-tier model clears the {kind} floor at "
                    f"difficulty {difficulty}")
            sel = select_model(PROBE_TASKS[kind][m["tier"]], kind=kind,
                               difficulty=difficulty,
                               stakes=PROBE_STAKES[m["tier"]],
                               allow=[m["id"], foil])
            base, _ = split_variant(sel.variant_id)
            expect(base in (m["id"], foil), f"escaped allow: {sel.variant_id}")
            if base != m["id"]:
                raise AssertionError(
                    f"Jev preferred {base} ({tiers[base]}) over {m['id']} "
                    f"({m['tier']}) for a {m['tier']}-tier {kind} task")
            return f"{kind}: {sel.variant_id} over {foil} @ {sel.confidence}"
        check(f"{m['id']}: reachable as a routing outcome", reach)
        if i < len(models) - 1:
            time.sleep(GAP)


# ------------------------------------------------------------------ live

def live(reg):
    print(f"\nlive routing checks ({GAP}s between calls)")
    tiers = {m["id"]: m["tier"] for m in enabled(reg)}
    step = {e: c["step"] for e, c in reg["efforts"].items()}

    def low():
        sel = select_model("rename a local variable in one file", stakes="low",
                           priorities=["cost", "latency", "quality"])
        expect(tiers[sel.model_id] == "fast",
               f"routed to {tiers[sel.model_id]} tier ({sel.variant_id})")
        expect(step[sel.effort] <= 0,
               f"trivial task got {sel.effort} effort (step {step[sel.effort]})")
        return f"{sel.variant_id} @ {sel.confidence}"
    check("trivial task -> fast tier at low effort", low)

    time.sleep(GAP)

    def high():
        sel = select_model(
            "redesign the payment reconciliation engine across 40 files with "
            "ambiguous requirements; must not lose transactions", stakes="high")
        expect(tiers[sel.model_id] == "deep",
               f"routed to {tiers[sel.model_id]} tier ({sel.variant_id})")
        expect(step[sel.effort] >= 0,
               f"complex task got {sel.effort} effort (step {step[sel.effort]})")
        return f"{sel.variant_id} @ {sel.confidence}"
    check("complex high-stakes task -> deep tier at raised effort", high)

    time.sleep(GAP)

    def effort_filter():
        sel = select_model("write a regex for ISO dates", stakes="low",
                           efforts=["low"])
        expect(sel.effort == "low", f"efforts filter leaked {sel.effort}")
        return sel.variant_id
    check("efforts filter is honoured end to end", effort_filter)

    time.sleep(GAP)

    def ctx():
        known = sorted({m["context_tokens"] for m in enabled(reg)
                        if m.get("context_tokens")})
        expect(len(known) >= 2, "need two distinct context windows to test this")
        # Above the smallest window, so the filter has something to drop, but
        # not above the largest, which would leave nothing to route to.
        threshold = known[0] + 1
        sel = select_model("summarise a very large document", stakes="low",
                           min_context_tokens=threshold)
        window = sel.model.get("context_tokens")
        expect(window is None or window >= threshold,
               f"{sel.variant_id} has a known window smaller than {threshold}")
        return f"survived min_context_tokens={threshold:,} -> {sel.variant_id}"
    check("min_context_tokens is honoured end to end", ctx)

    time.sleep(GAP)

    def allow():
        ids = ["claude-sonnet-5@low", "claude-haiku-4-5-20251001@low"]
        sel = select_model("write a regex for ISO dates", stakes="low", allow=ids)
        expect(sel.variant_id in ids, f"{sel.variant_id} outside allow={ids}")
        return f"stayed inside allow -> {sel.variant_id}"
    check("allow of exact variants is honoured end to end", allow)

    time.sleep(GAP)

    def tool():
        r = call("select_model", {"task": "summarise a 3-line changelog",
                                  "stakes": "low"})
        expect("error" not in r, f"tool call failed: {r}")
        for k in ("variant_id", "model_id", "effort", "confidence", "cost"):
            expect(k in r, f"missing {k}")
        json.dumps(r)
        return f"{r['variant_id']} @ {r['confidence']}"
    check("select_model tool call returns a serialisable result", tool)

    time.sleep(GAP)

    def bad_key():
        # Swap the credential the backend in force actually reads. Clobbering
        # JEV_API_KEY while the openrouter backend is active proves nothing:
        # that call would succeed on the key it really uses.
        from .client import resolve_backend
        backend = resolve_backend()
        names = {"native": ("MODEL_ROUTER_API_KEY", "JEV_API_KEY"),
                 "openrouter": ("OPENROUTER_API_KEY",)}[backend]
        saved = {k: os.environ.get(k) for k in names}
        for k in names:
            os.environ[k] = "jev_bogus"
        try:
            select_model("x", stakes="low")
        except JevError as e:
            expect(e.status == 401, f"expected 401, got {e.status}: {e}")
            return f"401 surfaced as JevError on the {backend} backend"
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
        raise AssertionError("bad key should raise")
    check("invalid key surfaces as a 401 JevError", bad_key)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    p.add_argument("--models", action="store_true")
    p.add_argument("--no-smoke", action="store_true")
    p.add_argument("--all", action="store_true")
    args = p.parse_args()

    reg = load_registry()
    offline(reg)
    offline_settings("jev_model_router")
    offline_config("jev_model_router")
    offline_backends(reg)
    if args.models or args.all:
        per_model(reg, smoke=not args.no_smoke)
    if args.live or args.all:
        live(reg)
        per_model_routing(reg)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed, {len(SKIP)} skipped")
    for name, why in SKIP:
        print(f"  ~ {name}: {why}")
    for name, err in FAIL:
        print(f"  - {name}: {err}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
