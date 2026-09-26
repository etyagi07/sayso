"""Logging in keeps the session and nothing else.

Credentials are typed at each login and must never reach the disk; the
session file must be private from the moment it exists; and SHOONYA_*
shell variables must not decide which account is used. Runs offline.
"""

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shoonya.client as client  # noqa: E402
from shoonya import credentials  # noqa: E402

SECRET = "s" * 63 + "x"
CREDS = {"client_id": "AB1234_U", "user_id": "AB1234", "secret": SECRET}


def logged_in(env=None):
    """Run a login against stubs. -> (session dir, what the exchange got)."""
    folder = Path(tempfile.mkdtemp())
    got = {}
    saved = (client.SESSION_FILE, credentials.ask, client.webbrowser.open,
             client.input if hasattr(client, "input") else None,
             dict(os.environ))
    client.SESSION_FILE = folder / ".session.json"
    credentials.ask = lambda: dict(CREDS)
    client.webbrowser.open = lambda url: None
    client.input = lambda prompt="": "http://127.0.0.1:8787/?code=CODE1"
    os.environ.update(env or {})
    api = client.Shoonya()
    api.getOAuthURL = lambda url, cid: got.setdefault("url_client", cid)

    def exchange(code, secret, client_id, user_id):
        got.update(code=code, secret=secret, client_id=client_id,
                   user_id=user_id)
        return ("TOKEN", "AB1234", "REFRESH", "AB1234")
    api.getAccessToken = exchange
    old_umask = os.umask(0o022)
    try:
        api.login_interactive(open_browser=False)
    finally:
        os.umask(old_umask)
        (client.SESSION_FILE, credentials.ask, client.webbrowser.open,
         _, env_before) = saved
        del client.input
        os.environ.clear()
        os.environ.update(env_before)
    return folder, got


def test_credentials_never_reach_the_disk():
    folder, got = logged_in()
    assert got["secret"] == SECRET and got["client_id"] == "AB1234_U"
    files = list(folder.iterdir())
    assert [f.name for f in files] == [".session.json"], files
    text = files[0].read_text()
    for value in (SECRET, "AB1234_U", "REFRESH"):
        assert value not in text, f"{value!r} was written to the session"
    assert json.loads(text) == {"access_token": "TOKEN", "uid": "AB1234",
                                "actid": "AB1234"}


def test_session_file_is_private():
    folder, _ = logged_in()
    mode = stat.S_IMODE((folder / ".session.json").stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_shell_variables_do_not_choose_the_account():
    # An exported SHOONYA_* once logged in to one account while the
    # screen named another. They are ignored now: the typed values win.
    _, got = logged_in({"SHOONYA_CLIENT_ID": "ZZ9999_U",
                        "SHOONYA_USER_ID": "ZZ9999",
                        "SHOONYA_SECRET_CODE": "other"})
    assert got["client_id"] == "AB1234_U" and got["user_id"] == "AB1234"
    assert got["secret"] == SECRET


def test_private_write_replaces_an_open_file_safely():
    folder = Path(tempfile.mkdtemp())
    target = folder / "f.json"
    target.write_text("old")
    target.chmod(0o644)
    # Private from the first byte: look at the file as it appears, before
    # it is moved into place - a write-then-chmod shows 0644 here.
    seen = []
    real_replace = client.os.replace

    def spy(src, dst):
        seen.append(stat.S_IMODE(os.stat(src).st_mode))
        real_replace(src, dst)
    client.os.replace = spy
    old_umask = os.umask(0o022)
    try:
        client._write_private(target, "new")
    finally:
        client.os.replace = real_replace
        os.umask(old_umask)
    assert seen == [0o600], seen
    assert target.read_text() == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in folder.iterdir()] == ["f.json"]


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            passed += 1
            print(f"ok   {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
