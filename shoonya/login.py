"""Run once per trading day: python -m shoonya.login [--account NAME]

Asks for API credentials first if none are stored for that account.
"""

from shoonya import profile

profile.from_argv()

from shoonya.client import Shoonya  # noqa: E402

if __name__ == "__main__":
    print(f"Account: {profile.label()}")
    Shoonya(ask=True).login_interactive()
