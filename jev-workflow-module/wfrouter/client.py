"""Transport for the Jev decision API (https://www.jevai.org). Stdlib only.

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


class JevError(RuntimeError):
    """Any failed Jev call. .status is the HTTP code when there was one."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def post(path, payload, *, timeout=30, retries=3):
    """POST to the Jev API and return the `data` object. Retries 429 with backoff."""
    key = os.environ.get("JEV_API_KEY", "")
    if not key:
        raise JevError("set JEV_API_KEY in .env or the environment")

    base = os.environ.get("JEV_BASE_URL", "https://www.jevai.org").rstrip("/")
    body = json.dumps(payload).encode()
    if len(body) > 32 * 1024:
        raise JevError(f"payload is {len(body)} bytes; the Jev API caps bodies at 32 KiB")

    req = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            # Cloudflare rejects the default urllib User-Agent with error 1010.
            "User-Agent": "wfrouter/1.0",
        },
    )

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                parsed = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            # 429: the API rate-limits bursts and sends no Retry-After.
            # 5xx: transient upstream errors; 502 has been seen in practice.
            transient = e.code == 429 or 500 <= e.code < 600
            if transient and attempt < retries - 1:
                time.sleep(2 ** attempt * 5)
                continue
            raise JevError(f"HTTP {e.code}: {detail}", status=e.code) from None
        except Exception as e:
            raise JevError(str(e)) from None

    if parsed.get("code") != 0:
        raise JevError(f"code={parsed.get('code')}: {parsed.get('message')}")
    return parsed["data"]
