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


MODULE = "jev_data_policy"
# The prefix that answers a setting for this module alone. It drops the shared
# "jev_" so a scoped name never reads like one of the JEV_ names every module
# shares: MODEL_ROUTER_BACKEND against JEV_BACKEND.
SCOPE = MODULE.upper().removeprefix("JEV_")
CONFIG_NAME = "jev.json"
_CONFIG = None

# Refused in jev.json: that file is checked in, so a key placed there would be
# committed. Secrets stay in .env, which is not.
CONFIG_SECRETS = ("key", "token", "secret", "password")

# Structural keys in jev.json, never settings. "modules" holds the per-module
# table, and one module's setting really is called MODULES (the workflows
# directory) -- without this, a lookup for it would find the table instead.
CONFIG_RESERVED = ("modules",)


class PolicyError(ValueError):
    """Base for every refusal this module makes."""


class NotApprovedError(PolicyError):
    """No target is cleared to receive data of the requested class."""


class ExternalLeakError(PolicyError):
    """Sending this text to an external service would exceed its clearance."""


def dotenv_paths(name=".env"):
    """Where to look for a project file, nearest caller first.

    Walks up from the working directory, so a module sitting in a subfolder
    still finds the one at its project's root. A file shipped inside the
    package is the last resort, so the host project's own configuration always
    wins over one that travelled with the module.
    """
    paths, d = [], os.getcwd()
    while True:
        paths.append(os.path.join(d, name))
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    paths.append(os.path.join(PKG_DIR, name))
    return paths


def load_dotenv(name=".env"):
    """Load KEY=value lines from .env without overriding the real environment."""
    for path in dotenv_paths(name):
        try:
            with open(path) as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_dotenv()


def load_config(name=CONFIG_NAME):
    """The project-root config, found the same way .env is. Cached."""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    _CONFIG = {}
    for path in dotenv_paths(name):
        try:
            with open(path) as f:
                loaded = json.load(f)
        except OSError:
            continue
        except ValueError as e:
            raise PolicyError(f"{path} is not valid JSON: {e}")
        if not isinstance(loaded, dict):
            raise PolicyError(f"{path} must hold an object")
        _check_no_secrets(loaded, path)
        _CONFIG = loaded
        break
    return _CONFIG


def _check_no_secrets(config, path):
    scopes = [config] + list((config.get("modules") or {}).values())
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        for key in scope:
            # A leading underscore marks a note rather than a setting; the
            # resolver never reads those, so they are not credentials.
            if key.startswith("_"):
                continue
            # Substring, not an exact name: openrouter_api_key must be caught
            # as surely as api_key, or the resolver becomes a way to read a
            # credential out of a committed file.
            if any(word in key.lower() for word in CONFIG_SECRETS):
                raise PolicyError(
                    f"{path} sets {key!r}; that file is checked in, so a "
                    f"credential there would be committed. Put it in .env")


def setting_source(name, default=None):
    """The value for `name` and where it came from, for showing the user.

    Same resolution as every other module here: DATA_POLICY_<NAME> beats
    JEV_<NAME>, which beats jev.json's modules.jev_data_policy entry, which beats its
    top level, which beats the built-in default.
    """
    for env_key in (f"{SCOPE}_{name}", f"JEV_{name}"):
        value = os.environ.get(env_key)
        if value:
            return value, f"${env_key}"
    config = load_config()
    key = name.lower()
    scoped = (config.get("modules") or {}).get(MODULE) or {}
    if isinstance(scoped, dict) and scoped.get(key):
        return scoped[key], f"{CONFIG_NAME} modules.{MODULE}.{key}"
    if key not in CONFIG_RESERVED and config.get(key):
        return config[key], f"{CONFIG_NAME} {key}"
    return default, "the policy shipped with this module"


def setting(name, default=None):
    """Read one setting: env first, then the root config, then the default."""
    return setting_source(name, default)[0]


def policy_path():
    """Which policy file is in force, and where that choice came from."""
    return setting_source("DATA_POLICY", os.path.join(PKG_DIR, "policy.json"))


def load(path=None):
    """Return the validated policy."""
    path = path or policy_path()[0]
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
