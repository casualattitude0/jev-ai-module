#!/usr/bin/env python3
"""Verification suite for the data policy module.

    python3 -m jev_data_policy.verify

Offline only: this module makes no network calls by design.
"""
import copy
import json
import os
import sys
import tempfile

from . import policy as P
from .policy import (CONFIG_NAME, ExternalLeakError, NotApprovedError,
                     PolicyError, approved_targets, assert_service,
                     assert_target, guarded_call, load, load_config,
                     policy_path, rank, require_targets, setting_source)

PASS, FAIL = [], []


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


def _rejects(obj, fragment):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(obj, f)
        path = f.name
    try:
        load(path)
    except PolicyError as e:
        expect(fragment.lower() in str(e).lower(), f"wrong message: {e}")
        return
    finally:
        os.unlink(path)
    raise AssertionError(f"expected PolicyError containing {fragment!r}")


def main():
    pol = load()
    classes = pol["classes"]
    floor = pol["external_class_floor"]
    print(f"data policy checks ({len(classes)} classes, floor {floor!r})")

    check("policy loads and validates",
          lambda: f"{len(pol['targets'])} targets, {len(pol['services'])} services")

    def ordering():
        for a, b in zip(classes, classes[1:]):
            expect(rank(a, pol) < rank(b, pol), f"{a} should rank below {b}")
        return " < ".join(classes)
    check("classes are ordered least to most sensitive", ordering)

    check("unknown class is rejected", lambda: _raises(
        lambda: rank("top-secret", pol), PolicyError, "unknown data class"))

    for bad, frag, why in [
        ("dup", "duplicate", "duplicate target id"),
        ("noid", "no id", "target without an id"),
        ("class", "unknown class", "approval for an unknown class"),
        ("floor", "not a known class", "floor outside the class list"),
    ]:
        def mk(bad=bad, frag=frag):
            b = copy.deepcopy(pol)
            if bad == "dup":
                b["targets"].append(copy.deepcopy(b["targets"][0]))
            elif bad == "noid":
                del b["targets"][0]["id"]
            elif bad == "class":
                b["targets"][0]["approved_classes"] = ["cosmic"]
            elif bad == "floor":
                b["external_class_floor"] = "cosmic"
            _rejects(b, frag)
        check(f"{why} rejected", mk)

    def fail_closed_absent():
        for c in classes[1:]:
            expect(not P._clears(None, c, pol),
                   f"an unlisted target was cleared for {c}")
        expect(P._clears(None, "public", pol), "public should be allowed")
        return "unlisted means public-only"
    check("a target absent from the policy fails closed", fail_closed_absent)

    def fail_closed_empty():
        entry = {"id": "x"}
        for c in classes[1:]:
            expect(not P._clears(entry, c, pol),
                   f"a target with no approved_classes was cleared for {c}")
        return "missing approved_classes means public-only"
    check("a target with no approved_classes fails closed", fail_closed_empty)

    def none_preapproved():
        for c in classes[1:]:
            got = approved_targets(c, pol)
            expect(not got, f"pre-approved above public for {c}: {got}")
        expect(approved_targets("public", pol), "public should list every target")
        return "every target ships public-only"
    check("no target is pre-approved above public", none_preapproved)

    def approvals_attributed():
        for t in pol["targets"] + pol["services"]:
            above = [c for c in (t.get("approved_classes") or [])
                     if rank(c, pol) > 0]
            if above:
                expect(t.get("approved_by"),
                       f"{t['id']} is approved for {above} with approved_by unset")
        return "anything above public names who approved it"
    check("approvals above public are attributed", approvals_attributed)

    def raising_works():
        p2 = copy.deepcopy(pol)
        p2["targets"][0]["approved_classes"] = ["regulated"]
        got = approved_targets("regulated", p2)
        expect(got == [p2["targets"][0]["id"]], f"unexpected: {got}")
        # ...and clearance is inclusive downwards
        expect(p2["targets"][0]["id"] in approved_targets("internal", p2),
               "a regulated-cleared target should also clear internal")
        return "raising one target clears it for that class and below"
    check("raising approved_classes clears a target", raising_works)

    check("assert_target refuses an uncleared target", lambda: _raises(
        lambda: assert_target("claude-opus-5", "regulated", pol),
        NotApprovedError, "not approved"))

    def require_names_the_gap():
        try:
            require_targets("regulated", minimum=2, pol=pol)
        except NotApprovedError as e:
            expect("operator decision" in str(e), f"unhelpful: {e}")
            return "names the gap and who can close it"
        raise AssertionError("should raise")
    check("require_targets refuses with an actionable message", require_names_the_gap)

    def service_floor():
        for c in classes[rank(floor, pol):]:
            try:
                assert_service("jevai.org", c, pol=pol)
            except ExternalLeakError:
                continue
            raise AssertionError(f"{c} was sent to an unapproved service")
        return f"blocked at and above {floor!r}"
    check("external service refuses text at or above the floor", service_floor)

    def service_below_floor():
        # Build the approval rather than relying on one shipping in policy.json:
        # nothing above public should be pre-approved there.
        p2 = copy.deepcopy(pol)
        svc = p2["services"][0]
        svc["approved_classes"] = classes[:rank(floor, p2)]
        svc["approved_by"] = "verify-fixture"
        for c in svc["approved_classes"]:
            assert_service(svc["id"], c, pol=p2)
        return f"an approved service accepts {svc['approved_classes']}"
    check("an approved service accepts text within its clearance",
          service_below_floor)

    def shipped_service_is_closed():
        for svc in pol.get("services") or []:
            for c in classes[1:]:
                try:
                    assert_service(svc["id"], c, pol=pol)
                except ExternalLeakError:
                    continue
                raise AssertionError(
                    f"{svc['id']} ships approved for {c} without review")
        return "every shipped service is public-only"
    check("no service is pre-approved above public", shipped_service_is_closed)

    def redaction_unlocks():
        assert_service("jevai.org", classes[-1], redacted=True, pol=pol)
        return "redacted=True permits the most sensitive class"
    check("redaction unlocks the external call", redaction_unlocks)

    def unknown_service_fails_closed():
        try:
            assert_service("some-other-saas.example", "internal", pol=pol)
        except ExternalLeakError:
            return "an unlisted service is public-only"
        raise AssertionError("unlisted service should fail closed")
    check("an unlisted service fails closed", unknown_service_fails_closed)

    def injection():
        seen = {}

        def fake_select(**kw):
            seen.update(kw)
            return "chosen"

        p2 = copy.deepcopy(pol)
        for t in p2["targets"][:2]:
            t["approved_classes"] = ["internal"]
        p2["services"][0]["approved_classes"] = ["internal"]
        p2["services"][0]["approved_by"] = "verify-fixture"
        out = guarded_call(fake_select, data_class="internal",
                           service=p2["services"][0]["id"], pol=p2,
                           task="x", stakes="low")
        expect(out == "chosen", "consumer was not called")
        expect(seen["allow"] == [t["id"] for t in p2["targets"][:2]],
               f"allow-list wrong: {seen.get('allow')}")
        expect(seen["task"] == "x" and seen["stakes"] == "low",
               "consumer kwargs were mangled")
        return f"allow={seen['allow']}"
    check("guarded_call narrows the consumer's allow-list", injection)

    def injection_blocks():
        called = []
        try:
            guarded_call(lambda **kw: called.append(kw), data_class="regulated",
                         service="jevai.org", pol=pol, task="x")
        except PolicyError:
            expect(not called, "the consumer ran despite a policy refusal")
            return "consumer never ran"
        raise AssertionError("should raise")
    check("guarded_call refuses before calling the consumer", injection_blocks)

    def no_imports():
        src = open(os.path.join(P.PKG_DIR, "policy.py")).read()
        for name in ("jev_model_router", "requests", "httpx", "urllib"):
            expect(f"import {name}" not in src,
                   f"policy.py imports {name}; it must stay standalone")
        return "no consumer or network imports"
    check("the module stays standalone", no_imports)

    def dotenv_is_loaded():
        # This module read os.environ but never loaded .env, so the
        # JEV_DATA_POLICY that .env.example documents silently did nothing.
        src = open(os.path.join(P.PKG_DIR, "policy.py")).read()
        expect("load_dotenv()" in src, "policy.py never loads .env")
        return ".env is loaded on import, like every other module here"
    check("the module reads the project's .env", dotenv_is_loaded)

    def named_in_the_root_config():
        config = load_config()
        expect(config, f"no {CONFIG_NAME} found from {os.getcwd()}")
        entry = (config.get("modules") or {}).get("jev_data_policy")
        expect(entry is not None,
               f"{CONFIG_NAME} does not name this module; someone reading it "
               f"at the project root would not know this module exists")
        expect("backend" not in entry,
               "this module makes no Jev calls and must not claim a backend")
        return f"{CONFIG_NAME} names it, with no backend to choose"
    check(f"the project-root {CONFIG_NAME} names this module", named_in_the_root_config)

    def resolution_order():
        saved_env = {k: os.environ.pop(k, None)
                     for k in ("DATA_POLICY_DATA_POLICY", "JEV_DATA_POLICY")}
        saved_cfg = P._CONFIG
        try:
            P._CONFIG = {}
            path, source = policy_path()
            expect(path == os.path.join(P.PKG_DIR, "policy.json"),
                   f"the shipped policy should be the fallback, got {path}")

            P._CONFIG = {"modules": {"jev_data_policy": {"data_policy": "/from/file.json"}}}
            path, source = policy_path()
            expect(path == "/from/file.json", f"the root file was ignored: {path}")
            expect("modules.jev_data_policy" in source, f"wrong source: {source}")

            os.environ["JEV_DATA_POLICY"] = "/from/shared-env.json"
            expect(policy_path()[0] == "/from/shared-env.json",
                   "env should beat the file")
            os.environ["DATA_POLICY_DATA_POLICY"] = "/from/scoped-env.json"
            expect(policy_path()[0] == "/from/scoped-env.json",
                   "the scoped name should win")
            return "scoped env > shared env > jev.json > shipped policy"
        finally:
            P._CONFIG = saved_cfg
            for k, v in saved_env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("the policy in force resolves in the documented order", resolution_order)

    def no_bare_env_reads():
        import glob, re
        offenders = []
        for path in glob.glob(os.path.join(P.PKG_DIR, "*.py")):
            if os.path.basename(path) == "verify.py":
                continue
            src = open(path).read()
            offenders += [f"{os.path.basename(path)}:{n}" for n in
                          re.findall(r'os\.environ(?:\.get)?[\(\[]"([A-Z_]+)"', src)]
        expect(not offenders, f"settings read straight from the env: {offenders}")
        return "every setting goes through the resolver"
    check("no setting bypasses the resolver", no_bare_env_reads)

    def refuses_committed_secrets():
        saved = P._CONFIG
        d = tempfile.mkdtemp()
        with open(os.path.join(d, CONFIG_NAME), "w") as f:
            json.dump({"api_key": "jev_x"}, f)
        cwd = os.getcwd()
        try:
            P._CONFIG = None
            os.chdir(d)
            load_config()
        except PolicyError as e:
            expect("committed" in str(e) or ".env" in str(e), f"wrong error: {e}")
            return "a credential in the checked-in file is refused"
        finally:
            os.chdir(cwd)
            P._CONFIG = saved
        raise AssertionError("should raise")
    check(f"{CONFIG_NAME} refuses credentials", refuses_committed_secrets)

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
