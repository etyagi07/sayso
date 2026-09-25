"""Collect Shoonya API credentials, and optionally remember them.

Run: python -m shoonya.credentials

These are the three values from the broker's API app registration. The
secret code is typed hidden, the way a password should be, and saving is
a choice rather than a requirement - some people would rather type it
each day than leave it on disk.
"""

import getpass
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

FIELDS = [
    ("SHOONYA_CLIENT_ID", "Client ID", "usually your user ID plus a suffix, e.g. ABC123_U", False),
    ("SHOONYA_USER_ID", "User ID", "the ID you log in to Shoonya with", False),
    ("SHOONYA_SECRET_CODE", "Secret code", "64 characters, shown once at registration", True),
]

G, R, Y, DIM, X = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"


def read_env_file():
    values = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def have_credentials():
    """True when all three values are available from file or environment."""
    stored = read_env_file()
    return all(os.environ.get(k) or stored.get(k) for k, *_ in FIELDS)


def write_env_file(values):
    """Write .env, preserving anything else already in it."""
    existing = {}
    other_lines = []
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key = line.split("=", 1)[0].strip()
            if key in {k for k, *_ in FIELDS}:
                continue
            if line.strip():
                other_lines.append(line)
    existing.update(values)

    body = "\n".join(other_lines)
    if body:
        body += "\n"
    body += "\n".join(f"{k}={existing[k]}" for k, *_ in FIELDS) + "\n"
    ENV_FILE.write_text(body)
    # Owner-only: this file is enough to trade the account.
    ENV_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)


def prompt(save=None):
    """Ask for the three values. Returns them, and saves if asked to."""
    stored = read_env_file()
    print(f"\n{DIM}Shoonya API credentials{X}")
    print(f"{DIM}  From your API app registration at shoonya.com.{X}")
    print(f"{DIM}  Leave blank to keep an existing value.{X}\n")

    values = {}
    for key, label, hint, secret in FIELDS:
        current = stored.get(key, "")
        shown = ""
        if current:
            shown = (f" [{'*' * 8}]" if secret
                     else f" [{current}]")
        while True:
            question = f"  {label}{shown}: "
            entered = (getpass.getpass(question) if secret
                       else input(question)).strip()
            if not entered and current:
                entered = current
            if entered:
                break
            print(f"    {DIM}{hint}{X}")
        values[key] = entered

    secret_len = len(values["SHOONYA_SECRET_CODE"])
    if secret_len != 64:
        print(f"\n{Y}  Note: the secret code is usually 64 characters; "
              f"this one is {secret_len}.{X}")

    if save is None:
        answer = input(f"\n  Save these to .env for next time? "
                       f"[{G}Y{X}/n]: ").strip().lower()
        save = answer in ("", "y", "yes")

    if save:
        write_env_file(values)
        print(f"\n{G}  Saved to .env{X} {DIM}(readable only by you){X}")
        print(f"{DIM}  It is gitignored - never commit or share it.{X}\n")
    else:
        print(f"\n{DIM}  Not saved. These apply to this session only;{X}")
        print(f"{DIM}  you will be asked again next time.{X}\n")
        for k, v in values.items():
            os.environ[k] = v

    return values


def ensure(interactive=True):
    """Make sure credentials are available, asking for them if needed."""
    if have_credentials():
        return True
    if not interactive or not sys.stdin.isatty():
        return False
    prompt()
    return True


if __name__ == "__main__":
    prompt()
