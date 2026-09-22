"""Serve the input: actually call the model Jev selected.

One transport:
  cli - shell out to an agent CLI such as `claude` or `codex`

The model Jev picks is reached through its own CLI, using the CLI's own
logged-in session. No provider API key is read here, there is deliberately no
HTTP path to Anthropic, OpenAI or any gateway in front of them, and every API
key is stripped from the CLI's environment before it runs — otherwise the CLI
would authenticate with a key found in the shell, which is the same forbidden
thing by a longer route.

Pick with serve(..., transport=...), the JEV_DISPATCH env var, or a model's
"transport" field in models.json. "cli" is the only accepted value today; the
seam stays so another transport can be added deliberately rather than by
default.
"""
import os
import shutil
import subprocess
import tempfile

from .client import setting

DEFAULT_CLI_TIMEOUT = 300


def cli_timeout():
    """Seconds a model CLI may run. Resolved like every other setting here."""
    value = setting("CLI_TIMEOUT", DEFAULT_CLI_TIMEOUT)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DispatchError(
            f"CLI_TIMEOUT must be a whole number of seconds, got {value!r}"
        ) from None

# Stripped from the CLI's environment before it runs. An agent CLI will happily
# authenticate with one of these if it finds it, which would be exactly the
# thing this module must not do: reach Claude or GPT with an API key. The CLI
# has to use its own logged-in session instead, so a key sitting in the shell
# or in .env cannot quietly turn into a metered API call.
STRIPPED_KEY_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    # Not a model key, but the CLI has no use for it either, and a credential
    # that never enters a subprocess cannot be logged or forwarded by one.
    "JEV_API_KEY",
    "MODEL_ROUTER_API_KEY",
)


def cli_env():
    """The environment a model CLI runs in: this one, minus every API key."""
    return {k: v for k, v in os.environ.items() if k not in STRIPPED_KEY_ENV}

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
            f"path; install it — serving goes through the model's own CLI, "
            f"and there is no API fallback to reach it another way"
        )

    timeout = cli_timeout()
    outfile = None
    if cfg.get("reply_from_file"):
        fd, outfile = tempfile.mkstemp(prefix="jev_model_router-", suffix=".txt")
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
            argv, input=prompt, capture_output=True, text=True,
            timeout=timeout, env=cli_env(),
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
            f"{cfg['command']} timed out after {timeout}s") from None
    except OSError as e:
        raise DispatchError(f"could not run {cfg['command']}: {e}") from None
    finally:
        if outfile:
            try:
                os.unlink(outfile)
            except OSError:
                pass


# ---------------------------------------------------------------- public

TRANSPORTS = ("cli",)


def resolve_transport(model, transport=None):
    """Which transport to use: explicit > env > model default > cli.

    The env step reads MODEL_ROUTER_DISPATCH before JEV_DISPATCH, so the
    repo-root .env can answer this for one module at a time.
    """
    choice = (transport
              or setting("DISPATCH")
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
    transport: "cli", the only one available. api_key is rejected: no key
    reaches a model from here.
    """
    model = selection.model
    level = effort or getattr(selection, "effort", None)
    resolve_transport(model, transport)
    if api_key:
        raise DispatchError(
            "no transport here takes an api_key; serving goes through the "
            "model's own CLI, which carries its own auth")
    return _cli_serve(model, prompt, system, max_tokens, level)
