#!/usr/bin/env python3
"""Verification suite for the data classifier.

    python3 -m jev_data_policy.verify          # offline: the ladder, the
                                               # payload, refusals, settings
    python3 -m jev_data_policy.verify --live   # also classify real content

The offline layer must always be green: it makes no network call and stubs the
transport, so nothing in it depends on Jev being up. --live sends the samples
below to Jev and is subject to rate limits and to the class descriptions being
good enough to separate neighbours.
"""
import copy
import json
import os
import sys
import tempfile

import importlib

from . import client as K

# `from . import classify` would bind the FUNCTION: the package re-exports
# classify() under the same name as the module it lives in. Reach for the
# module itself, so the stub below can replace the post() it calls.
C = importlib.import_module(".classify", __package__)
from .classify import (MAX_CONTENT_BYTES, Classification, PolicyError,
                       class_ids, classes_path, classify, load, payload, rank)
from .client import CONFIG_NAME, JevError, load_config, resolve_jev_model, setting_source

PASS, FAIL = [], []

# Content that should land on a given class, used by --live. Each one is
# written to be unambiguous about what it CONTAINS, which is the only thing
# this module judges.
LIVE = [
    ("Our open-source parser supports UTF-8 and ships under the MIT licence.",
     "public"),
    ("Q3 roadmap, not announced yet: we cut the co-op mode and move the "
     "launch to March. Margin on the deluxe SKU is 62%.", "internal"),
    ("User wang.mei@example.com, phone 0912-345-678, lives at 3F No. 7 "
     "Section 2 Roosevelt Road, Taipei.", "confidential"),
    ("Debug dump from the staging box: session cookie "
     "sid=8f3c1ad9e4b27760, expires in one hour.", "confidential"),
    ("Production key AKIA4NPRODEXAMPLE7QZ with secret "
     "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY, full write access to the "
     "live billing account.", "regulated"),
    ("Patient A123456789, born 1978-04-02, diagnosed with type 2 diabetes; "
     "billed to card 4111 1111 1111 1111.", "regulated"),
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


def _raises(fn, exc, fragment):
    try:
        fn()
    except exc as e:
        expect(fragment.lower() in str(e).lower(), f"wrong message: {e}")
        return "raised as expected"
    raise AssertionError(f"expected {exc.__name__}")


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


class _Stub:
    """Stands in for the transport so the offline layer never leaves the box."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.seen = answer, error, []

    def __call__(self, path, body, **kw):
        self.seen.append((path, body, kw))
        if self.error:
            raise self.error
        return {"answers": {C.QUESTION: self.answer}}


def _with_stub(stub, fn):
    real = C.post
    C.post = stub
    try:
        return fn()
    finally:
        C.post = real


def offline(spec):
    ids = class_ids(spec)
    print(f"data classifier checks ({len(ids)} classes: {' < '.join(ids)})")

    check("the class ladder loads and validates",
          lambda: f"{len(spec['classes'])} classes, instructions set")

    def ordering():
        for a, b in zip(ids, ids[1:]):
            expect(rank(a, spec) < rank(b, spec), f"{a} should rank below {b}")
        return " < ".join(ids)
    check("classes are ordered least to most sensitive", ordering)

    check("an unknown class is rejected", lambda: _raises(
        lambda: rank("top-secret", spec), PolicyError, "unknown data class"))

    for bad, frag, why in [
        ("empty", "no 'classes'", "a class file with no classes"),
        ("noid", "no id", "a class without an id"),
        ("nodesc", "no description", "a class with no description"),
        ("dup", "duplicate", "a duplicate class id"),
        ("noinstr", "no 'instructions'", "a class file with no instructions"),
    ]:
        def mk(bad=bad, frag=frag):
            b = copy.deepcopy(spec)
            if bad == "empty":
                b["classes"] = []
            elif bad == "noid":
                del b["classes"][0]["id"]
            elif bad == "nodesc":
                b["classes"][0]["description"] = ""
            elif bad == "dup":
                b["classes"].append(copy.deepcopy(b["classes"][0]))
            elif bad == "noinstr":
                del b["instructions"]
            _rejects(b, frag)
        check(f"{why} is rejected", mk)

    def descriptions_are_about_content():
        # The one rule that keeps this module's job narrow: a class is defined
        # by what the content holds, never by who may receive it. If a vendor
        # or an approval ever appears here, the approval registry has grown
        # back.
        forbidden = ("approved", "vendor", "allow-list", "allowlist",
                     "target", "cleared", "model")
        for c in spec["classes"]:
            low = c["description"].lower()
            hit = [w for w in forbidden if w in low]
            expect(not hit,
                   f"{c['id']}'s description talks about {hit}; a class is what "
                   f"the content contains, not who may receive it")
        return "no class is defined by who may receive it"
    check("classes describe content, not recipients", descriptions_are_about_content)

    def payload_shape():
        body = payload("hello", spec, context={"source": "test"})
        q = body["questions"][C.QUESTION]
        expect(body["state"]["content"] == "hello", "content is not in the state")
        expect(body["state"]["context"] == {"source": "test"}, "context dropped")
        expect(q["type"] == "choice", f"wrong question type: {q['type']}")
        expect(set(q["criteria"]) == set(ids), "criteria are not the classes")
        expect(q["criteria"]["public"] == spec["classes"][0]["description"],
               "the criteria are not the class descriptions verbatim")
        return "one choice question over the ladder"
    check("the decision payload asks one choice over the classes", payload_shape)

    def answer_is_returned():
        stub = _Stub({"choice": "confidential", "confidence": 0.81,
                      "probabilities": {"confidential": 0.81, "internal": 0.19}})
        got = _with_stub(stub, lambda: classify("someone@example.com", spec=spec))
        expect(got.data_class == "confidential", f"wrong class: {got.data_class}")
        expect(got.confidence == 0.81, "confidence dropped")
        expect(got.probabilities["internal"] == 0.19, "probabilities dropped")
        expect(not got.escalated, "nothing should have been escalated")
        expect(got.content_bytes == len("someone@example.com"), "size wrong")
        return "class, confidence and probabilities all come back"
    check("a decision is returned as a Classification", answer_is_returned)

    def preset_answer_shape():
        # The native generic endpoint answers with "choice"; the preset
        # endpoints call the same field "decision". Accept both.
        stub = _Stub({"decision": "internal", "confidence": 0.5})
        got = _with_stub(stub, lambda: classify("the roadmap", spec=spec))
        expect(got.data_class == "internal", f"wrong class: {got.data_class}")
        return "both 'choice' and 'decision' are read"
    check("either answer shape is accepted", preset_answer_shape)

    def unknown_answer_refused():
        stub = _Stub({"choice": "cosmic", "confidence": 0.99})
        _with_stub(stub, lambda: _raises(
            lambda: classify("x", spec=spec), PolicyError, "unknown data class"))
        return "a class nobody defined is refused, not passed on"
    check("an answer outside the ladder is refused", unknown_answer_refused)

    def missing_answer_refused():
        stub = _Stub({})
        _with_stub(stub, lambda: _raises(
            lambda: classify("x", spec=spec), PolicyError, "no answer"))
        return "an empty answer raises rather than defaulting"
    check("an empty answer is refused", missing_answer_refused)

    def transport_failure_propagates():
        # The failure mode that matters: a Jev outage must never come back as
        # a cheerful 'public'.
        stub = _Stub(error=JevError("HTTP 503: down", status=503))
        _with_stub(stub, lambda: _raises(
            lambda: classify("x", spec=spec), JevError, "503"))
        return "an outage raises; it never answers 'public'"
    check("a failed call fails closed", transport_failure_propagates)

    def escalates_when_unsure():
        stub = _Stub({"choice": "internal", "confidence": 0.4,
                      "probabilities": {"internal": 0.4, "confidential": 0.35,
                                        "regulated": 0.25}})
        got = _with_stub(stub, lambda: classify("x", spec=spec, min_confidence=0.7))
        expect(got.data_class == "regulated", f"escalated to {got.data_class}")
        expect(got.escalated_from == "internal", "did not record what Jev said")
        expect(got.confidence == 0.4, "confidence should stay Jev's own")
        return "0.4 < 0.7 -> raised internal to regulated, both recorded"
    check("an unsure answer is raised, not rounded down", escalates_when_unsure)

    def escalation_never_lowers():
        stub = _Stub({"choice": "regulated", "confidence": 0.3,
                      "probabilities": {"regulated": 0.3, "public": 0.7}})
        got = _with_stub(stub, lambda: classify("x", spec=spec, min_confidence=0.9))
        expect(got.data_class == "regulated",
               f"escalation lowered the class to {got.data_class}")
        expect(not got.escalated, "nothing was raised, so nothing to record")
        return "a low-confidence 'regulated' stays regulated"
    check("escalation only ever raises the class", escalation_never_lowers)

    def escalates_without_probabilities():
        stub = _Stub({"choice": "public", "confidence": 0.1})
        got = _with_stub(stub, lambda: classify("x", spec=spec, min_confidence=0.5))
        expect(got.data_class == ids[-1],
               f"no evidence for anything, yet it chose {got.data_class}")
        return f"no probabilities at all -> {ids[-1]}"
    check("no probabilities escalates to the top", escalates_without_probabilities)

    def verbatim_by_default():
        stub = _Stub({"choice": "public", "confidence": 0.05})
        got = _with_stub(stub, lambda: classify("x", spec=spec))
        expect(got.data_class == "public", "default should not escalate")
        return "min_confidence defaults to 0.0: Jev is reported verbatim"
    check("without min_confidence the answer is Jev's own", verbatim_by_default)

    def at_least():
        c = Classification("confidential", 0.9)
        expect(c.at_least("internal", spec), "confidential >= internal")
        expect(c.at_least("confidential", spec), "confidential >= confidential")
        expect(not c.at_least("regulated", spec), "confidential < regulated")
        return "at_least() compares by rank, not by name"
    check("a Classification can be compared to a threshold", at_least)

    def oversize_refused():
        big = "a" * (MAX_CONTENT_BYTES + 1)
        stub = _Stub({"choice": "public", "confidence": 1.0})
        _with_stub(stub, lambda: _raises(
            lambda: classify(big, spec=spec), PolicyError, "split it"))
        expect(not stub.seen, "oversize content was sent anyway")
        return "refused before the call, not truncated"
    check("oversize content is refused rather than trimmed", oversize_refused)

    def empty_refused():
        _raises(lambda: classify("   ", spec=spec), PolicyError, "empty")
        return "whitespace is not content"
    check("empty content is refused", empty_refused)

    def bad_threshold_refused():
        _raises(lambda: classify("x", spec=spec, min_confidence=1.5),
                PolicyError, "0..1")
        return "a threshold outside 0..1 is refused"
    check("an impossible min_confidence is refused", bad_threshold_refused)

    def no_approvals_left():
        # This module used to keep a register of who was cleared to receive
        # what. That is someone else's decision; if any of it grows back here,
        # the module has two jobs again.
        gone = ("approved_targets", "assert_target", "assert_service",
                "guarded_call", "require_targets", "approved_classes",
                "external_class_floor")
        src = "".join(open(os.path.join(C.PKG_DIR, f)).read()
                      for f in ("classify.py", "__init__.py", "classes.json"))
        hit = [n for n in gone if n in src]
        expect(not hit, f"the approval registry grew back: {hit}")
        return "no clearance, no allow-list, no targets"
    check("the module only classifies", no_approvals_left)

    def knows_no_vendors():
        src = open(os.path.join(C.PKG_DIR, "classify.py")).read()
        for name in ("jev_model_router", "jev_workflow", "anthropic", "openai"):
            expect(name not in src,
                   f"classify.py mentions {name}; it must know no consumer "
                   f"and no vendor")
        return "no consumer and no vendor is named"
    check("the module stays standalone", knows_no_vendors)

    def dotenv_is_loaded():
        src = open(os.path.join(C.PKG_DIR, "client.py")).read()
        expect("load_dotenv()" in src, "client.py never loads .env")
        return ".env is loaded on import, like every other module here"
    check("the module reads the project's .env", dotenv_is_loaded)

    def key_reaches_jev_only():
        saved = os.environ.pop("JEV_OPENROUTER_MODEL", None)
        savedc = os.environ.pop("DATA_POLICY_OPENROUTER_MODEL", None)
        try:
            expect(resolve_jev_model().startswith("~typesafe/jev"),
                   "the default model is not a Jev model")
            for slug in ("openai/gpt-5.6-sol", "anthropic/claude-opus-5"):
                os.environ["JEV_OPENROUTER_MODEL"] = slug
                _raises(resolve_jev_model, JevError, "not a Jev decisions model")
            return "a chat slug is refused; the key cannot be repointed"
        finally:
            os.environ.pop("JEV_OPENROUTER_MODEL", None)
            for k, v in (("JEV_OPENROUTER_MODEL", saved),
                         ("DATA_POLICY_OPENROUTER_MODEL", savedc)):
                if v is not None:
                    os.environ[k] = v
    check("the OpenRouter key can only reach Jev", key_reaches_jev_only)

    def named_in_the_root_config():
        config = load_config()
        expect(config, f"no {CONFIG_NAME} found from {os.getcwd()}")
        entry = (config.get("modules") or {}).get("jev_data_policy")
        expect(entry is not None,
               f"{CONFIG_NAME} does not name this module; someone reading it "
               f"at the project root would not know this module exists")
        expect(entry.get("backend") in K.BACKENDS,
               f"this module now asks Jev, so {CONFIG_NAME} must say which "
               f"path it takes; got {entry.get('backend')!r}")
        return f"{CONFIG_NAME} names it, backend={entry['backend']}"
    check(f"the project-root {CONFIG_NAME} names this module", named_in_the_root_config)

    def resolution_order():
        saved = {k: os.environ.pop(k, None)
                 for k in ("DATA_POLICY_DATA_POLICY", "JEV_DATA_POLICY")}
        saved_cfg = K._CONFIG
        try:
            K._CONFIG = {}
            path, _ = classes_path()
            expect(path == os.path.join(C.PKG_DIR, "classes.json"),
                   f"the shipped ladder should be the fallback, got {path}")

            K._CONFIG = {"modules": {"jev_data_policy":
                                     {"data_policy": "/from/file.json"}}}
            path, source = classes_path()
            expect(path == "/from/file.json", f"the root file was ignored: {path}")
            expect("modules.jev_data_policy" in source, f"wrong source: {source}")

            os.environ["JEV_DATA_POLICY"] = "/from/shared-env.json"
            expect(classes_path()[0] == "/from/shared-env.json",
                   "env should beat the file")
            os.environ["DATA_POLICY_DATA_POLICY"] = "/from/scoped-env.json"
            expect(classes_path()[0] == "/from/scoped-env.json",
                   "the scoped name should win")
            return "scoped env > shared env > jev.json > shipped ladder"
        finally:
            K._CONFIG = saved_cfg
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("the class file in force resolves in the documented order",
          resolution_order)

    def backend_resolution():
        saved = {k: os.environ.pop(k, None)
                 for k in ("DATA_POLICY_BACKEND", "JEV_BACKEND")}
        saved_cfg = K._CONFIG
        try:
            K._CONFIG = {}
            expect(K.resolve_backend() == "native", "native should be the default")
            expect(K.resolve_backend("openrouter") == "openrouter",
                   "an explicit backend should win")
            _raises(lambda: K.resolve_backend("elsewhere"), JevError, "must be one of")
            K._CONFIG = {"modules": {"jev_data_policy": {"backend": "openrouter"}}}
            expect(K.resolve_backend() == "openrouter", "the root file was ignored")
            os.environ["JEV_BACKEND"] = "native"
            expect(K.resolve_backend() == "native", "env should beat the file")
            os.environ["DATA_POLICY_BACKEND"] = "openrouter"
            expect(K.resolve_backend() == "openrouter", "the scoped name should win")
            return "argument > scoped env > shared env > jev.json > native"
        finally:
            K._CONFIG = saved_cfg
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    check("the backend resolves in the documented order", backend_resolution)

    def no_bare_env_reads():
        import glob
        import re
        allowed = {"OPENROUTER_API_KEY"}      # not a JEV_ setting; .env only
        offenders = []
        for path in glob.glob(os.path.join(C.PKG_DIR, "*.py")):
            if os.path.basename(path) == "verify.py":
                continue
            src = open(path).read()
            offenders += [f"{os.path.basename(path)}:{n}" for n in
                          re.findall(r'os\.environ(?:\.get)?[\(\[]"([A-Z_]+)"', src)
                          if n not in allowed]
        expect(not offenders, f"settings read straight from the env: {offenders}")
        return "every setting goes through the resolver"
    check("no setting bypasses the resolver", no_bare_env_reads)

    def refuses_committed_secrets():
        saved = K._CONFIG
        d = tempfile.mkdtemp()
        with open(os.path.join(d, CONFIG_NAME), "w") as f:
            json.dump({"api_key": "jev_x"}, f)
        cwd = os.getcwd()
        try:
            K._CONFIG = None
            os.chdir(d)
            load_config()
        except JevError as e:
            expect("committed" in str(e) or ".env" in str(e), f"wrong error: {e}")
            return "a credential in the checked-in file is refused"
        finally:
            os.chdir(cwd)
            K._CONFIG = saved
        raise AssertionError("should raise")
    check(f"{CONFIG_NAME} refuses credentials", refuses_committed_secrets)


def live(spec):
    backend, source = setting_source("BACKEND", "native")
    print(f"\nlive classification ({len(LIVE)} samples, backend {backend} "
          f"from {source})")
    for content, want in LIVE:
        def one(content=content, want=want):
            got = classify(content, spec=spec)
            expect(got.data_class == want,
                   f"got {got.data_class!r} ({got.confidence:.2f}), "
                   f"wanted {want!r}: {got.probabilities}")
            return f"{got.data_class} at {got.confidence:.2f}"
        check(f"{want:<13} {content[:44]}...", one)


def main():
    spec = load()
    offline(spec)
    if "--live" in sys.argv:
        live(spec)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for name, err in FAIL:
        print(f"  - {name}: {err}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
