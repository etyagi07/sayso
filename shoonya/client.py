"""Thin wrapper over NorenRestApiPy that handles OAuth + session caching."""

import json
import os
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
import NorenRestApiPy.NorenApi as _sdk
from NorenRestApiPy.NorenApi import NorenApi

HOST = "https://api.shoonya.com/NorenWClientAPI/"

# (connect, read) seconds, for every call to the broker.
TIMEOUT = (5, 15)


class _TimedRequests:
    """`requests`, as the SDK sees it, but with a timeout on every call.

    None of the SDK's thirty network calls sets one, so a stalled
    connection hangs quotes, logins and account reads indefinitely - it
    froze a live session during testing. Swapping the module the SDK
    calls through covers every call without editing the SDK.
    """

    def __getattr__(self, name):
        return getattr(requests, name)

    def post(self, *args, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        return requests.post(*args, **kwargs)

    def get(self, *args, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        return requests.get(*args, **kwargs)


_sdk.requests = _TimedRequests()
WS = "wss://api.shoonya.com/NorenWSAPI/"
AUTHORIZE_URL = "https://api.shoonya.com/OAuthlogin/authorize/oauth"

from shoonya import profile

ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = profile.session_file()


class Shoonya(NorenApi):
    """The SDK client. Holds no credentials - only a session, once attached."""

    def __init__(self):
        super().__init__(host=HOST, websocket=WS)

    # --- auth -------------------------------------------------------------

    def login_interactive(self, open_browser=True):
        """Ask for credentials, log in, and keep only the day's session."""
        from shoonya import credentials

        creds = credentials.ask()
        url = self.getOAuthURL(AUTHORIZE_URL, creds["client_id"])
        print(f"\n1. Open this URL and log in:\n\n   {url}\n")
        if open_browser:
            webbrowser.open(url)
        pasted = input("2. Paste the redirect URL (or just the code): ").strip()
        code = _extract_code(pasted)

        result, detail = _exchange(self, code, creds)
        creds.clear()           # used once; not kept around in memory
        if not result:
            raise SystemExit(
                f"Token exchange failed for code {code!r}.\n"
                f"  Broker said: {detail}\n"
                f"  Auth codes are single-use and expire in minutes - "
                f"log in again for a fresh one."
            )

        # The refresh token is dropped: the SDK cannot use it, and anything
        # not stored cannot leak.
        access_token, uid, _refresh, actid = result
        session = {"access_token": access_token, "uid": uid, "actid": actid}
        _write_private(SESSION_FILE, json.dumps(session, indent=2))
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


def _write_private(path, text):
    """Write a file only its owner can read - from the first byte.

    Writing then chmod-ing leaves a moment where the file has default
    permissions. Creating it 0600 and renaming it into place does not.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def session_user():
    """The user ID of the saved session, or None. No network call."""
    try:
        return json.loads(SESSION_FILE.read_text()).get("uid")
    except (OSError, ValueError):
        return None


def _exchange(api, code, creds):
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
        result = api.getAccessToken(code, creds["secret"],
                                    creds["client_id"], creds["user_id"])
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
