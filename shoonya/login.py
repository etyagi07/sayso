"""Run once per session (tokens are day-scoped): python -m shoonya.login"""

from shoonya.client import Shoonya

if __name__ == "__main__":
    Shoonya().login_interactive()
