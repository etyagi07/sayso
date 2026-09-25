"""Thin wrapper over NorenRestApiPy that handles OAuth + session caching."""

import json
import os
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from NorenRestApiPy.NorenApi import NorenApi

HOST = "https://api.shoonya.com/NorenWClientAPI/"
WS = "wss://api.shoonya.com/NorenWSAPI/"
AUTHORIZE_URL = "https://api.shoonya.com/OAuthlogin/authorize/oauth"

ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = ROOT / ".session.json"


def _load_dotenv_force():
    """Re-read .env, overriding what is already in the environment.

    Used after credentials are entered interactively, so the new values
    take effect in a process that started without them.
    """
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()


def _load_dotenv():
    """Populate os.environ from .env, without pulling in python-dotenv."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


class Shoonya(NorenApi):
    def __init__(self, ask=False):
        super().__init__(host=HOST, websocket=WS)
        if ask:
            from shoonya import credentials
            credentials.ensure()
            _load_dotenv_force()
        self.client_id = _env("SHOONYA_CLIENT_ID")
        self.user_id = _env("SHOONYA_USER_ID")
        self.secret_code = _env("SHOONYA_SECRET_CODE")

    # --- auth -------------------------------------------------------------

    def authorize_url(self):
        return self.getOAuthURL(AUTHORIZE_URL, self.client_id)

    def login_interactive(self, open_browser=True):
        """Print the authorize URL, take the redirect back, save the session."""
        url = self.authorize_url()
        print(f"\n1. Open this URL and log in:\n\n   {url}\n")
        if open_browser:
            webbrowser.open(url)
        pasted = input("2. Paste the redirect URL (or just the code): ").strip()
        code = _extract_code(pasted)

        result, detail = _exchange(self, code)
        if not result:
            raise SystemExit(
                f"Token exchange failed for code {code!r}.\n"
                f"  Broker said: {detail}\n"
                f"  Auth codes are single-use and expire in minutes - "
                f"log in again for a fresh one."
            )

        access_token, uid, refresh_token, actid = result
        session = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "uid": uid,
            "actid": actid,
        }
        SESSION_FILE.write_text(json.dumps(session, indent=2))
        SESSION_FILE.chmod(0o600)
        print(f"\nLogged in as {uid} (account {actid}). Session cached in {SESSION_FILE.name}.")
        return session

    def resume(self):
        """Reattach a cached session. Returns False if there isn't a usable one."""
        if not SESSION_FILE.exists():
            return False
        s = json.loads(SESSION_FILE.read_text())
        self.injectOAuthHeader(s["access_token"], s["uid"], s["actid"])
        return True


def connect(interactive=True):
    """Resume a cached session, falling back to a fresh OAuth login."""
    api = Shoonya()
    if api.resume() and _session_alive(api):
        return api
    if not interactive:
        raise RuntimeError("No valid cached session; run `python -m shoonya.login` first.")
    api.login_interactive()
    return api


def _exchange(api, code):
    """Exchange the code, capturing the broker's reply on failure.

    The SDK logs the response at DEBUG and returns None, so a failure
    otherwise gives you nothing to act on.
    """
    import logging

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("NorenRestApiPy.NorenApi")
    handler = _Capture()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        result = api.getAccessToken(code, api.secret_code,
                                    api.client_id, api.user_id)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    detail = next((r for r in reversed(records)
                   if "emsg" in r or "Error" in r), None)
    return result, detail or (records[-1] if records else "no response logged")


def _session_alive(api):
    """A cheap authenticated call; anything but a clean Ok means re-login.

    The SDK returns a dict on failure too - {"stat": "Not_Ok", "emsg":
    "Session Expired"} - so testing for None lets a dead session through
    and every later call fails in a confusing way.
    """
    try:
        res = api.get_limits()
    except Exception:
        return False
    if not isinstance(res, dict):
        return False
    return res.get("stat") == "Ok"


def _env(name):
    try:
        return os.environ[name]
    except KeyError:
        raise SystemExit(
            f"Missing {name}.\n"
            f"  Run: python -m shoonya.credentials"
        )


def _extract_code(pasted):
    """Pull the auth code out of whatever the browser gave you.

    Accepts the full redirect URL, a bare `code=...` fragment, or the code
    on its own - people paste all three, and the difference is invisible
    until the exchange fails with an unhelpful error.
    """
    pasted = pasted.strip().strip('"\'')
    if pasted.startswith("http"):
        params = parse_qs(urlparse(pasted).query)
        if "code" not in params:
            raise SystemExit(f"No `code` param in that URL: {pasted}")
        return params["code"][0]
    if "code=" in pasted:
        return pasted.split("code=", 1)[1].split("&")[0]
    return pasted
