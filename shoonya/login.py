"""Run once per trading day: python -m shoonya.login

Asks for API credentials first if none are stored.
"""

from shoonya.client import Shoonya

if __name__ == "__main__":
    Shoonya(ask=True).login_interactive()
