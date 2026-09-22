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
            post("/api/v1/decisions/completion", {"objective": "x" * 40000})
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


def offline_dispatch(reg):
    from .dispatch import (DEFAULT_CLI, THINKING_BUDGET, api_key_for,
                                    resolve_transport)

    def no_key():
        saved = {k: os.environ.pop(k, None)
                 for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
        try:
            m = next(m for m in enabled(reg) if m["provider"] == "anthropic")
            try:
                serve(_sel(m), "hi", transport="api")
            except DispatchError as e:
                expect("ANTHROPIC_API_KEY" in str(e), f"wrong error: {e}")
                return "names the missing key"
            raise AssertionError("should raise")
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
    check("api transport without a provider key fails clearly", no_key)

    def unknown_provider():
        for t, want in (("cli", "no CLI configured"), ("api", "no api adapter")):
            try:
                serve(_sel({"provider": "acme"}), "hi", transport=t)
            except DispatchError as e:
                expect(want in str(e), f"{t}: wrong error: {e}")
                continue
            raise AssertionError(f"{t}: should raise")
        return "both transports name the gap"
    check("unknown provider fails clearly on both transports", unknown_provider)

    def precedence():
        saved = os.environ.pop("JEV_DISPATCH", None)
        try:
            expect(resolve_transport({}) == "cli", "default should be cli")
            expect(resolve_transport({"transport": "api"}) == "api", "model override")
            expect(resolve_transport({"transport": "api"}, "cli") == "cli", "arg wins")
            os.environ["JEV_DISPATCH"] = "api"
            expect(resolve_transport({}) == "api", "env override")
            return "arg > env > model > cli"
        finally:
            os.environ.pop("JEV_DISPATCH", None)
            if saved is not None:
                os.environ["JEV_DISPATCH"] = saved
    check("transport precedence is arg > env > model > cli default", precedence)

    def bad_transport():
        try:
            serve(_sel({"provider": "anthropic"}), "hi", transport="pigeon")
        except DispatchError as e:
            expect("transport must be one of" in str(e), f"wrong error: {e}")
            return "rejected"
        raise AssertionError("should raise")
    check("unknown transport rejected", bad_transport)

    def key_on_cli():
        try:
            serve(_sel({"provider": "anthropic"}), "hi", transport="cli",
                  api_key="sk-test")
        except DispatchError as e:
            expect("only used by the api transport" in str(e), f"wrong error: {e}")
            return "api_key not silently ignored"
        raise AssertionError("should raise")
    check("api_key passed to the cli transport is rejected", key_on_cli)

    def key_precedence():
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            m = {"provider": "anthropic"}
            expect(api_key_for(m, "explicit") == "explicit", "explicit should win")
            os.environ["ANTHROPIC_API_KEY"] = "from-env"
            expect(api_key_for(m) == "from-env", "env fallback")
            expect(api_key_for(m, "explicit") == "explicit", "explicit still wins")
            return "explicit > env"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved
    check("api_key resolution prefers the explicit argument", key_precedence)

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
        for e in ("low", "medium", "high", "xhigh", "max"):
            expect(e in THINKING_BUDGET, f"no thinking budget for {e}")
        expect(THINKING_BUDGET["max"] >= THINKING_BUDGET["high"] >= THINKING_BUDGET["low"],
               "thinking budgets not monotonic")
        return "argv templates well-formed, budgets monotonic"
    check("effort reaches both transports", effort_flags)

    def codex_argv():
        cfg = DEFAULT_CLI["openai"]
        argv = [a.format(effort="high") for a in cfg["effort_args"]]
        expect(argv == ["-c", "model_reasoning_effort=high"],
               f"codex effort argv is {argv}; -c and its value must be separate")
        return "-c and key=value are separate argv elements"
    check("codex effort flag splits into two argv elements", codex_argv)

    def binary_resolution():
        from .dispatch import resolve_command
        for provider, cfg in DEFAULT_CLI.items():
            found = resolve_command(cfg)
            if found is None:
                continue
            expect(os.access(found, os.X_OK), f"{provider}: {found} not executable")
        return "resolves PATH then fallback paths"
    check("CLI binaries resolve via PATH or fallback paths", binary_resolution)

    def every_effort_mappable():
        declared = {e for m in enabled(reg) for e in m["efforts"]}
        for e in declared:
            expect(e in THINKING_BUDGET, f"effort {e!r} has no api mapping")
        return f"all {len(declared)} declared efforts map to the api transport"
    check("every declared effort is dispatchable", every_effort_mappable)


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


def per_model_routing(reg):
    models = enabled(reg)
    tiers = {m["id"]: m["tier"] for m in models}
    tasks = {
        "fast": "change a log message string in one file",
        "balanced": "add pagination to an existing REST endpoint and its tests",
        "deep": "redesign the transaction ledger across the service with "
                "ambiguous requirements and no downtime",
    }
    stakes = {"fast": "low", "balanced": "medium", "deep": "high"}

    print(f"\nper-model routing reachability ({len(models)} models, {GAP}s apart)")
    for i, m in enumerate(models):
        foil = next((x["id"] for x in models if x["tier"] != m["tier"]), None)
        if foil is None:
            continue
        if i:
            time.sleep(GAP)

        def reach(m=m, foil=foil):
            sel = select_model(tasks[m["tier"]], stakes=stakes[m["tier"]],
                               allow=[m["id"], foil])
            base, _ = split_variant(sel.variant_id)
            expect(base in (m["id"], foil), f"escaped allow: {sel.variant_id}")
            if base != m["id"]:
                raise AssertionError(
                    f"Jev preferred {base} ({tiers[base]}) over {m['id']} "
                    f"({m['tier']}) for a {m['tier']}-tier task")
            return f"{sel.variant_id} over {foil} @ {sel.confidence}"
        check(f"{m['id']}: reachable as a routing outcome", reach)


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
        known = [m for m in enabled(reg) if m.get("context_tokens")]
        big = max(m["context_tokens"] for m in known) + 1
        sel = select_model("summarise a very large document", stakes="low",
                           min_context_tokens=big)
        expect(sel.model.get("context_tokens") is None,
               f"{sel.variant_id} has a known window smaller than {big}")
        return f"survived min_context_tokens={big:,} -> {sel.variant_id}"
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
        saved = os.environ["JEV_API_KEY"]
        os.environ["JEV_API_KEY"] = "jev_bogus"
        try:
            select_model("x", stakes="low")
        except JevError as e:
            expect(e.status == 401, f"expected 401, got {e.status}: {e}")
            return "401 surfaced as JevError"
        finally:
            os.environ["JEV_API_KEY"] = saved
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
