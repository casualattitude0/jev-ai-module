"""Transport for Jev decisions. Stdlib only.

Two backends answer the same calls:
  native     - the Jev API at https://www.jevai.org, with JEV_API_KEY
  openrouter - the same Jev model published on OpenRouter as
               ~typesafe/jev-latest, with OPENROUTER_API_KEY

That key reaches Jev and nothing else: the model slug is checked before every
call, so it cannot be pointed at a chat model. Claude and GPT are never called
with an API key anywhere in this module.

Pick with JEV_BACKEND, or WORKFLOW_BACKEND for this module alone. Default is
native. This module asks only generic {state, questions} decisions, which both
backends speak directly, so nothing is translated on the way.

A copy of the transport in jev-model-router-module: modules here stay self-
contained and never import one another."""
import json
import os
import time
import urllib.error
import urllib.request

PKG_DIR = os.path.dirname(os.path.abspath(__file__))


def dotenv_paths(name=".env"):
    """Where to look for .env, nearest caller first.

    Walks up from the working directory, so a module sitting in a subfolder
    still finds the .env at its project's root. A file shipped inside the
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


MODULE = "jev_workflow"
# The prefix that answers a setting for this module alone. It drops the shared
# "jev_" so a scoped name never reads like one of the JEV_ names every module
# shares: MODEL_ROUTER_BACKEND against JEV_BACKEND.
SCOPE = MODULE.upper().removeprefix("JEV_")


CONFIG_NAME = "jev.json"
_CONFIG = None

# Refused in jev.json: the file is checked in, so a key placed here would be
# committed. Secrets stay in .env, which is not.
CONFIG_SECRETS = ("key", "token", "secret", "password")

# Structural keys in jev.json, never settings. "modules" holds the per-module
# table, and one module's setting really is called MODULES (the workflows
# directory) -- without this, a lookup for it would find the table instead.
CONFIG_RESERVED = ("modules",)


def load_config(name=CONFIG_NAME):
    """The project-root config: which path each module's decisions take.

    Found the same way .env is — walking up from the working directory — so
    `cd <module> && python3 -m <pkg>` still reads the one at the project root.
    Cached after the first read.
    """
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
            raise JevError(f"{path} is not valid JSON: {e}")
        if not isinstance(loaded, dict):
            raise JevError(f"{path} must hold an object")
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
                raise JevError(
                    f"{path} sets {key!r}; that file is checked in, so a "
                    f"credential there would be committed. Put it in .env")


def _config_value(name):
    """This module's entry in the root config, then the file's top level."""
    config = load_config()
    key = name.lower()
    scoped = (config.get("modules") or {}).get(MODULE) or {}
    if isinstance(scoped, dict) and scoped.get(key):
        return scoped[key], f"{CONFIG_NAME} modules.{MODULE}.{key}"
    if key not in CONFIG_RESERVED and config.get(key):
        return config[key], f"{CONFIG_NAME} {key}"
    return None, None


def setting_source(name, default=None):
    """The value for `name` and where it came from, for showing the user."""
    for env_key in (f"{SCOPE}_{name}", f"JEV_{name}"):
        value = os.environ.get(env_key)
        if value:
            return value, f"${env_key}"
    value, source = _config_value(name)
    if value is not None:
        return value, source
    return default, "built-in default"


def setting(name, default=None):
    """Read one setting: env first, then the root config, then the default.

    A setting can be answered for one module or for all of them, in either
    place: WORKFLOW_BACKEND beats JEV_BACKEND, and in jev.json
    modules.jev_workflow.backend beats the top-level backend. Environment wins over
    the file, so a one-off run never means editing a committed file.
    """
    return setting_source(name, default)[0]


class JevError(RuntimeError):
    """Any failed Jev call. .status is the HTTP code when there was one."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _request(url, body, headers, *, timeout, retries):
    """POST json and return the parsed response. Retries 429 and 5xx."""
    data = json.dumps(body).encode()
    if len(data) > 32 * 1024:
        raise JevError(f"payload is {len(data)} bytes; a decision body caps at 32 KiB")

    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 # Cloudflare rejects the default urllib User-Agent with 1010.
                 "User-Agent": "jev_workflow/1.0", **headers},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            # 429: bursts are rate-limited and no Retry-After is sent.
            # 5xx: transient upstream errors; 502 has been seen in practice.
            transient = e.code == 429 or 500 <= e.code < 600
            if transient and attempt < retries - 1:
                time.sleep(2 ** attempt * 5)
                continue
            raise JevError(f"HTTP {e.code}: {detail}", status=e.code) from None
        except Exception as e:
            raise JevError(str(e)) from None


GENERIC_PATH = "/api/v1/decisions"
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
OPENROUTER_MODEL = "~typesafe/jev-latest"

# The OpenRouter key may only ever reach a Jev decisions model. The model is
# configurable so a Jev version can be pinned, and that knob is the one place
# where a slug like "openai/gpt-5.6-sol" could otherwise point this key at a
# chat model — which is the thing that must not happen.
JEV_MODEL_PREFIXES = ("~typesafe/jev",)


def resolve_jev_model():
    """The Jev model to ask on OpenRouter. Refuses anything that is not one."""
    slug = setting("OPENROUTER_MODEL", OPENROUTER_MODEL)
    if not slug.startswith(JEV_MODEL_PREFIXES):
        raise JevError(
            f"{slug!r} is not a Jev decisions model; the OpenRouter key is for "
            f"Jev decisions only and must not reach a chat model. Use a slug "
            f"starting with {' or '.join(JEV_MODEL_PREFIXES)}")
    return slug


BACKENDS = ("native", "openrouter")


def resolve_backend(backend=None):
    """Which backend answers a decision: explicit > env > native."""
    choice = backend or setting("BACKEND") or "native"
    if choice not in BACKENDS:
        raise JevError(f"backend must be one of {BACKENDS}, got {choice!r}")
    return choice


def _native_post(path, payload, *, timeout, retries):
    key = setting("API_KEY", "")
    if not key:
        raise JevError(
            f"set JEV_API_KEY (or {SCOPE}_API_KEY for this module "
            f"alone) in .env or the environment")

    base = setting("BASE_URL", "https://www.jevai.org").rstrip("/")
    parsed = _request(f"{base}{path}", payload,
                      {"Authorization": f"Bearer {key}"},
                      timeout=timeout, retries=retries)
    if parsed.get("code") != 0:
        raise JevError(f"code={parsed.get('code')}: {parsed.get('message')}")
    return parsed["data"]


def _openrouter_post(path, payload, *, timeout, retries):
    if path != GENERIC_PATH:
        raise JevError(
            f"the openrouter backend serves the generic decisions endpoint "
            f"only; {path!r} has no equivalent there")

    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise JevError("set OPENROUTER_API_KEY to use the openrouter backend")

    parsed = _request(
        OPENROUTER_DECISIONS_URL,
        {"model": resolve_jev_model(),
         "state": payload.get("state"), "questions": payload.get("questions") or {}},
        {"Authorization": f"Bearer {key}"},
        timeout=timeout, retries=retries,
    )
    err = parsed.get("error")
    if err:
        message = err.get("message") if isinstance(err, dict) else err
        raise JevError(f"openrouter: {message}")
    return {"answers": parsed.get("answers") or {}}


def post(path, payload, *, timeout=30, retries=3, backend=None):
    """Ask for one decision and return the `data` object.

    The backend is the same choice for every call in a process unless one is
    passed here: WORKFLOW_BACKEND, then JEV_BACKEND, then native.
    """
    if resolve_backend(backend) == "openrouter":
        return _openrouter_post(path, payload, timeout=timeout, retries=retries)
    return _native_post(path, payload, timeout=timeout, retries=retries)
