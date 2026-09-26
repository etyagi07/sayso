"""Ask for the Shoonya API credentials at login. Nothing is saved.

The three values from the broker's API app registration are typed at each
login, used once to exchange the login code for a session, and dropped.
They never touch the disk and are never read from the environment - a
stored secret is a secret that can leak, and an exported SHOONYA_* shell
variable once meant logging in to one account while the screen named
another.

What IS kept is the day's session token (see shoonya.client), which dies
with the trading day on its own.
"""

import getpass
import warnings

from shoonya import profile

G, R, Y, DIM, X = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"


def _clean(text):
    """Trim what terminals and copy-paste add: spaces, quotes, stray slashes."""
    return text.strip().strip("\\/").strip().strip('"\'')


def _ask_plain(label, hint):
    while True:
        entered = _clean(input(f"  {label}: "))
        if entered:
            return entered
        print(f"    {DIM}{hint}{X}")


def _ask_secret(label):
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
                entered = _clean(getpass.getpass(f"  {label}: "))
        except (getpass.GetPassWarning, OSError):
            entered = ""

        if entered:
            return _dedupe_paste(entered)

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
    # Only worth checking when the input is longer than a single secret
    # could plausibly be. Otherwise a legitimately repetitive value gets
    # flagged as a double paste.
    if len(text) <= 80:
        return text

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


def ask():
    """Ask for the three values. -> {"client_id", "user_id", "secret"}."""
    print(f"\n{DIM}Shoonya API credentials - account: {profile.label()}{X}")
    print(f"{DIM}  From your API app registration at shoonya.com. Used for "
          f"this login only - nothing is saved.{X}")
    print(f"{DIM}  Client ID usually ends in _U; the User ID does not.{X}\n")

    client = _ask_plain("Client ID", "usually your user ID plus a suffix, "
                                     "e.g. ABC123_U")
    user = _ask_plain("User ID", "the ID you log in to Shoonya with")
    secret = _ask_secret("Secret code")

    # The client ID is the user ID plus a suffix, so the user ID never
    # carries one. Typing the client ID into both is the common slip, and
    # startswith() alone does not catch it because they are then equal.
    if "_" in user or user == client or not client.startswith(user):
        guess = client.split("_")[0]
        print(f"\n{Y}  The Client ID normally starts with the User ID.{X}")
        print(f"{DIM}  You entered client {client!r}, user {user!r}.{X}")
        if guess and guess != user:
            answer = input(f"  Use {G}{guess}{X} as the User ID? "
                           f"[{G}Y{X}/n]: ").strip().lower()
            if answer in ("", "y", "yes"):
                user = guess
                print(f"    {DIM}user id set to {guess}{X}")

    if len(secret) != 64:
        print(f"\n{Y}  The secret code is usually 64 characters; this one "
              f"is {len(secret)}.{X}")
        answer = input(f"  Enter it again? [{G}Y{X}/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            secret = _ask_secret("Secret code")

    return {"client_id": client, "user_id": user, "secret": secret}
