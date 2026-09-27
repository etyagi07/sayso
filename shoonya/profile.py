"""Which trading account is in use.

The login session and the daily limit counters both belong to one
account. Keeping them per account means switching is a flag, not copying
files around - and a session can never leak from one account to another.
Credentials are not stored at all; they are typed at each login.

    python -m voice.main --account client     -> .session.client.json
    python -m voice.main                      -> .session.json (the default)

Must be read before anything else from this project is imported, since
file paths are fixed at import time. Entry points call from_argv() first.
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAR = "SAYSO_ACCOUNT"


def name():
    n = os.environ.get(VAR, "").strip()
    return n or None


def _tag():
    n = name()
    return f".{n}" if n else ""


def session_file():
    return ROOT / f".session{_tag()}.json"


def account_file():
    """Per-account settings that are not credentials - registered IPs."""
    return ROOT / f".account{_tag()}.json"


def limits_file():
    return ROOT / f".daily_limits{_tag()}.json"


def label():
    return name() or "default"


def from_argv(argv=None):
    """Take --account NAME off the command line and make it current."""
    argv = sys.argv if argv is None else argv
    for i, arg in enumerate(list(argv)):
        value = None
        if arg == "--account" and i + 1 < len(argv):
            value = argv[i + 1]
            del argv[i:i + 2]
        elif arg.startswith("--account="):
            value = arg.split("=", 1)[1]
            del argv[i]
        if value is not None:
            # It becomes part of a filename, so keep it to a safe shape.
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
                raise SystemExit(f"Account name {value!r} should be letters, "
                                 f"digits, - or _.")
            os.environ[VAR] = value
            break
    return name()
