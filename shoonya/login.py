"""Run once per trading day: python -m shoonya.login [--account NAME]

Asks for your API credentials, logs in, and keeps only the day's session.
The credentials themselves are never saved.
"""

from shoonya import profile

profile.from_argv()

from shoonya import network  # noqa: E402
from shoonya.client import Shoonya  # noqa: E402


def main():
    print(f"Account: {profile.label()}")
    if not network.registered():
        network.ask()
    state, message = network.check()
    if state == "mismatch":
        # Checked before the browser opens: a login from the wrong address
        # fails at the very end, with an error that doesn't say why.
        print(f"\n{network.R}{message}{network.X}")
        answer = input("\n  Log in anyway? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            raise SystemExit(1)
    elif state == "unknown":
        print(f"{network.DIM}{message}{network.X}")
    Shoonya().login_interactive()


if __name__ == "__main__":
    main()
