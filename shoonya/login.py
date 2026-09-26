"""Run once per trading day: python -m shoonya.login [--account NAME]

Asks for your API credentials, logs in, and keeps only the day's session.
The credentials themselves are never saved.
"""

from shoonya import profile

profile.from_argv()

from shoonya.client import Shoonya  # noqa: E402

if __name__ == "__main__":
    print(f"Account: {profile.label()}")
    Shoonya().login_interactive()
