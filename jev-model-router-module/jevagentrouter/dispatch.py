"""Serve the input: actually call the model Jev selected.

Two transports:
  cli  (default today) - shell out to an agent CLI such as `claude`
  api  (ready for later) - HTTPS with an api_key, no CLI needed

Pick with serve(..., transport=...), the JEV_DISPATCH env var, or a model's
"transport" field in models.json. Default is "cli".
"""
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

API_TIMEOUT = 120
CLI_TIMEOUT = int(os.environ.get("JEV_CLI_TIMEOUT", "300"))

# Used when a registry entry declares no "cli" block of its own.
# Each *_args entry is a list of argv templates, so a flag that takes its value
# as a separate argument and one that joins it both come out correct.
DEFAULT_CLI = {
    "anthropic": {
        "command": "claude",
        "args": ["-p", "--model", "{model_id}"],
        "effort_args": ["--effort", "{effort}"],      # low|medium|high|xhigh|max
        "system_args": ["--append-system-prompt", "{system}"],
    },
    "openai": {
        "command": "codex",
        # The CLI ships inside the ChatGPT desktop app and is often not on PATH.
        "fallback_paths": ["/Applications/ChatGPT.app/Contents/Resources/codex"],
        "args": ["exec", "--model", "{model_id}", "--skip-git-repo-check",
                 "--output-last-message", "{outfile}"],
        "effort_args": ["-c", "model_reasoning_effort={effort}"],
        "system_args": None,
        # codex streams progress and token counts to stdout; the real reply is
        # whatever it writes to the --output-last-message file.
        "reply_from_file": True,
    },
}

# Effort -> Anthropic thinking budget, in tokens, for the api transport.
THINKING_BUDGET = {"low": 0, "medium": 4000, "high": 12000,
                   "xhigh": 24000, "max": 32000}

# Used by the api transport. Key env var per provider.
API_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class DispatchError(RuntimeError):
    pass


# ------------------------------------------------------------------ cli

def _cli_config(model):
    cfg = model.get("cli") or DEFAULT_CLI.get(model.get("provider"))
    if not cfg:
        raise DispatchError(
            f"no CLI configured for provider {model.get('provider')!r}; "
            f"add a \"cli\" block to its models.json entry"
        )
    return cfg


def resolve_command(cfg):
    """Find the CLI binary: PATH first, then any configured fallback paths."""
    found = shutil.which(cfg["command"])
    if found:
        return found
    for path in cfg.get("fallback_paths", []):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def cli_available(model):
    """True if this model's CLI can be found."""
    try:
        return resolve_command(_cli_config(model)) is not None
    except DispatchError:
        return False


def _cli_serve(model, prompt, system, _max_tokens, effort=None):
    cfg = _cli_config(model)
    command = resolve_command(cfg)
    if command is None:
        raise DispatchError(
            f"{cfg['command']!r} not found on PATH or at any configured fallback "
            f"path; install it or switch to the api transport (JEV_DISPATCH=api)"
        )

    outfile = None
    if cfg.get("reply_from_file"):
        fd, outfile = tempfile.mkstemp(prefix="jevagentrouter-", suffix=".txt")
        os.close(fd)

    argv = [command] + [a.format(model_id=model["id"], outfile=outfile or "")
                        for a in cfg.get("args", [])]
    if effort:
        tmpl = cfg.get("effort_args")
        if not tmpl:
            raise DispatchError(
                f"{cfg['command']!r} has no effort flag configured; remove the "
                f"effort from the variant or add \"effort_args\" to its cli block"
            )
        argv += [a.format(effort=effort) for a in tmpl]
    if system:
        tmpl = cfg.get("system_args")
        if not tmpl:
            raise DispatchError(
                f"{cfg['command']!r} has no system-prompt flag configured")
        argv += [a.format(system=system) for a in tmpl]

    try:
        proc = subprocess.run(
            argv, input=prompt, capture_output=True, text=True, timeout=CLI_TIMEOUT
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            raise DispatchError(
                f"{cfg['command']} exited {proc.returncode}: {detail}")
        if outfile:
            with open(outfile) as f:
                return f.read().strip()
        return proc.stdout.strip()
    except subprocess.TimeoutExpired:
        raise DispatchError(
            f"{cfg['command']} timed out after {CLI_TIMEOUT}s") from None
    except OSError as e:
        raise DispatchError(f"could not run {cfg['command']}: {e}") from None
    finally:
        if outfile:
            try:
                os.unlink(outfile)
            except OSError:
                pass


# ------------------------------------------------------------------ api

def _http_json(url, payload, headers):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise DispatchError(
            f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}"
        ) from None
    except Exception as e:
        raise DispatchError(str(e)) from None


def api_key_for(model, api_key=None):
    """Resolve the api_key for a model: explicit argument, then env var."""
    provider = model.get("provider")
    if api_key:
        return api_key
    env = API_KEY_ENV.get(provider)
    key = os.environ.get(env) if env else None
    if not key:
        raise DispatchError(
            f"no api_key for provider {provider!r}; pass api_key= or set "
            f"{env or 'the provider key'}"
        )
    return key


def _api_anthropic(model, prompt, system, max_tokens, api_key, effort=None):
    payload = {
        "model": model["id"],
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    budget = THINKING_BUDGET.get(effort or "", 0)
    if budget:
        # max_tokens must exceed the thinking budget.
        payload["max_tokens"] = max(max_tokens, budget + 1024)
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
    if system:
        payload["system"] = system
    data = _http_json(
        os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages",
        payload,
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    return "".join(b.get("text", "") for b in data.get("content", []))


def _api_openai(model, prompt, system, max_tokens, api_key, effort=None):
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    payload = {"model": model["id"], "messages": messages,
               "max_completion_tokens": max_tokens}
    if effort:
        # The API takes low|medium|high; xhigh/max fold into high.
        payload["reasoning_effort"] = "high" if effort in ("xhigh", "max") else effort
    data = _http_json(
        os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        + "/chat/completions",
        payload,
        {"Authorization": f"Bearer {api_key}"},
    )
    return data["choices"][0]["message"]["content"]


API_PROVIDERS = {"anthropic": _api_anthropic, "openai": _api_openai}


def _api_serve(model, prompt, system, max_tokens, api_key=None, effort=None):
    fn = API_PROVIDERS.get(model.get("provider"))
    if fn is None:
        raise DispatchError(
            f"no api adapter for provider {model.get('provider')!r}; "
            f"add one to jevagentrouter/dispatch.py"
        )
    return fn(model, prompt, system, max_tokens, api_key_for(model, api_key), effort)


# ---------------------------------------------------------------- public

TRANSPORTS = ("cli", "api")


def resolve_transport(model, transport=None):
    """Which transport to use: explicit > env > model default > cli."""
    choice = (transport
              or os.environ.get("JEV_DISPATCH")
              or model.get("transport")
              or "cli")
    if choice not in TRANSPORTS:
        raise DispatchError(f"transport must be one of {TRANSPORTS}, got {choice!r}")
    return choice


def serve(selection, prompt, *, system=None, max_tokens=2048,
          transport=None, api_key=None, effort=None):
    """Send `prompt` to the selected variant and return its text reply.

    The effort comes from the Selection, so the level Jev chose is the level
    actually used. Pass effort= only to override it.
    transport: "cli" (default) or "api". api_key is only used by "api".
    """
    model = selection.model
    level = effort or getattr(selection, "effort", None)
    choice = resolve_transport(model, transport)
    if choice == "api":
        return _api_serve(model, prompt, system, max_tokens, api_key, level)
    if api_key:
        raise DispatchError("api_key is only used by the api transport")
    return _cli_serve(model, prompt, system, max_tokens, level)
