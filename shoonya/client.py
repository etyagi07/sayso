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
    def __init__(self):
        super().__init__(host=HOST, websocket=WS)
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

        result = self.getAccessToken(code, self.secret_code, self.client_id, self.user_id)
        if not result:
            raise RuntimeError("Token exchange failed - see the response logged above.")

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


def _session_alive(api):
    """A cheap authenticated call; anything other than a clean result means re-login."""
    try:
        return api.get_limits() is not None
    except Exception:
        return False


def _env(name):
    try:
        return os.environ[name]
    except KeyError:
        raise SystemExit(f"Missing env var {name}. Copy .env.example to .env and fill it in.")


def _extract_code(pasted):
    if pasted.startswith("http"):
        params = parse_qs(urlparse(pasted).query)
        if "code" not in params:
            raise SystemExit(f"No `code` param in that URL: {pasted}")
        return params["code"][0]
    return pasted
