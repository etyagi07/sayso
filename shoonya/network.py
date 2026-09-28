"""Is this computer on an internet address the API key accepts?

Every API call - login, reads, orders - is checked against the addresses
registered on the broker's Api Key Generation page (SEBI requires it for
API trading). Home broadband and phone hotspots change address without
warning, and the broker's refusal is unhelpful, so this checks up front
and says plainly what is wrong.

The registered addresses are asked for once per account and kept in the
account file - they are not credentials. The current address comes from a
public lookup service: one small request that tells that service your
address and nothing else. It is never on the order path.

    python -m shoonya.network                  # check now
    python -m shoonya.network set IP [BACKUP]  # change what is registered
"""

from shoonya import profile as _profile

_profile.from_argv()

import ipaddress  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402

import requests  # noqa: E402

from shoonya import profile  # noqa: E402

# IPv4-only on purpose: api.shoonya.com is IPv4-only, so that is the
# address the broker sees. A dual-stack lookup could report IPv6 instead.
LOOKUPS = ("https://api.ipify.org", "https://checkip.amazonaws.com")

G, R, Y, DIM, X = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"

WHERE = ("the Api Key Generation page on shoonya.com (Primary and Backup IP "
         "Address)")


def _normal(ip):
    """'2001:DB8::1' and '2001:db8:0::1' are one address; None if not one."""
    try:
        return str(ipaddress.ip_address(ip.strip()))
    except ValueError:
        return None


def current(timeout=3):
    """This computer's public address as the broker sees it, or None."""
    for url in LOOKUPS:
        try:
            ip = _normal(requests.get(url, timeout=timeout).text)
        except requests.RequestException:
            continue
        if ip:
            return ip
    return None


# --- the account's registered addresses --------------------------------------

def _load():
    try:
        return json.loads(profile.account_file().read_text())
    except (OSError, ValueError):
        return {}


def registered():
    """The addresses saved for this account, or [] if never set."""
    return [ip for ip in (_normal(i) for i in _load().get("registered_ips", []))
            if ip]


def save(ips):
    ips = [_normal(i) for i in ips if i and i.strip()]
    if not ips or None in ips:
        raise ValueError("not an IP address")
    from shoonya.client import _write_private
    data = _load()
    data["registered_ips"] = ips
    _write_private(profile.account_file(), json.dumps(data, indent=2))
    return ips


def ask():
    """Ask for the registered addresses. Called at login until they are set."""
    print(f"\n{DIM}Your API key only works from the internet addresses "
          f"registered on{X}")
    print(f"{DIM}{WHERE}. Copy them from there.{X}\n")
    while True:
        primary = input("  Primary IP Address: ").strip()
        if _normal(primary):
            break
        print(f"    {Y}{primary!r} isn't an IP address - e.g. "
              f"203.0.113.10{X}")
    while True:
        backup = input("  Backup IP Address (Enter if none): ").strip()
        if not backup or _normal(backup):
            break
        print(f"    {Y}{backup!r} isn't an IP address{X}")
    return save([primary, backup])


# --- checking -----------------------------------------------------------------

def check(lookup=None):
    """-> (state, message). state: ok, mismatch, unset or unknown."""
    ips = registered()
    if not ips:
        return "unset", ("No registered IP address saved for this account - "
                         f"run: {profile.cmd('shoonya.login')}")
    # Looked up at call time, not bound at import - so it can be stubbed.
    now = (lookup or current)()
    if now is None:
        return "unknown", ("Couldn't look up this computer's internet "
                           "address, so the IP check was skipped.")
    if now in ips:
        return "ok", f"on {now}, which your API key accepts"
    listed = " or ".join(ips)
    return "mismatch", (f"You're on {now}, but this API key only works from "
                        f"{listed}. The broker will refuse logins and "
                        f"orders. Switch to that network, or add {now} on "
                        f"{WHERE}, then run: "
                        + profile.cmd("shoonya.network", "set",
                                      *(ips[:1] + [now])))


# The broker's refusal is not documented, and has not been seen yet - so
# match the shapes it could take, and never claim more than "probably".
_IP_REFUSAL = re.compile(r"\bip\b|ip address|whitelist|white list|"
                         r"not allowed from|unauthori[sz]ed (?:ip|source)",
                         re.IGNORECASE)


def explain(message):
    """Plain words for a broker refusal that looks IP-related, else None."""
    if not message or not _IP_REFUSAL.search(str(message)):
        return None
    ips = registered()
    tail = f" It's registered for {' or '.join(ips)}." if ips else ""
    return ("That looks like the internet-address check: this computer's "
            "address probably isn't one your API key accepts." + tail +
            " Run: " + profile.cmd("shoonya.network"))


def _main(argv):
    if argv[:1] == ["set"] and 2 <= len(argv) <= 3:
        try:
            ips = save(argv[1:])
        except ValueError:
            print(f"{R}Not an IP address: {' '.join(argv[1:])}{X}")
            return 1
        print(f"Saved for {profile.label()}: {' and '.join(ips)}")
    elif argv:
        print(__doc__)
        return 1
    state, message = check()
    colour = {"ok": G, "mismatch": R}.get(state, Y)
    print(f"{colour}{state}{X}  {message}")
    return 0 if state in ("ok", "unknown") else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
