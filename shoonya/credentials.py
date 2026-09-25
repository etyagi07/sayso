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
import warnings
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


def _ask_plain(label, shown, current, hint):
    while True:
        entered = input(f"  {label}{shown}: ").strip()
        if not entered and current:
            return current
        if entered:
            return entered
        print(f"    {DIM}{hint}{X}")


def _ask_secret(label, shown, current):
    """Hidden entry, with a visible fallback.

    Some terminals will not deliver a paste to a hidden prompt, and
    because nothing echoes there is no way to tell it failed. Offer the
    visible path rather than leaving people stuck.
    """
    print(f"    {DIM}Nothing appears as you type - that is normal.{X}")
    print(f"    {DIM}Paste ONCE, then press Enter. "
          f"Stuck? Press Enter on an empty line.{X}")
    while True:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", getpass.GetPassWarning)
                entered = getpass.getpass(f"  {label}{shown}: ").strip()
        except (getpass.GetPassWarning, OSError):
            entered = ""

        if entered:
            entered = _dedupe_paste(entered)
            return entered
        if current:
            return current

        answer = input(f"    {Y}Show the text while you type or paste "
                       f"it?{X} [{G}Y{X}/n]: ").strip().lower()
        if answer not in ("", "y", "yes"):
            continue
        entered = input(f"  {label} (visible): ").strip()
        if entered:
            print(f"    {DIM}captured {len(entered)} characters - clear your "
                  f"screen afterwards if anyone can see it{X}")
            return entered


def _dedupe_paste(text):
    """Spot the same value pasted several times over.

    Hidden entry shows nothing, so it is easy to paste again thinking the
    first one did not register. The result is a valid-looking string that
    is simply the secret repeated, and the broker rejects it with an
    unhelpful error.
    """
    # Smallest repeating unit of a plausible credential length. Smallest
    # wins because 8 copies of a 64-char secret is also 2 copies of a
    # 256-char block, and 64 is the one actually wanted. The lower bound
    # stops "aaaa" being read as "a" repeated.
    for size in range(16, len(text) // 2 + 1):
        if len(text) % size:
            continue
        unit = text[:size]
        if unit * (len(text) // size) != text:
            continue
        copies = len(text) // size
        print(f"\n    {Y}That looks like the same {size}-character value "
              f"pasted {copies} times.{X}")
        answer = input(f"    Use a single copy? [{G}Y{X}/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            print(f"    {DIM}using {size} characters{X}")
            return unit
        break
    return text
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
        entered = (_ask_secret(label, shown, current) if secret
                   else _ask_plain(label, shown, current, hint))
        values[key] = entered

    secret_len = len(values["SHOONYA_SECRET_CODE"])
    if secret_len != 64:
        print(f"\n{Y}  The secret code is usually 64 characters; this one "
              f"is {secret_len}.{X}")
        answer = input(f"  Enter it again? [{G}Y{X}/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            values["SHOONYA_SECRET_CODE"] = _ask_secret("Secret code", "", "")

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
