"""The internet-address check. Offline: the lookup and files are stubbed.

The API key only works from registered addresses. Home broadband and
phone hotspots change address without warning, and the broker's refusal
doesn't say why - so it is checked up front, and explained when it bites.
"""

import json
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shoonya import network, profile  # noqa: E402


def account(ips=None):
    """Point the account file at a temp dir, optionally with saved IPs."""
    path = Path(tempfile.mkdtemp()) / ".account.json"
    profile.account_file = lambda: path
    if ips is not None:
        network.save(ips)
    return path


_real_account_file = profile.account_file


def _no_network(*a, **k):
    raise AssertionError("a test tried a real IP lookup")


network.requests.get = _no_network


def isolated(fn):
    def run():
        try:
            fn()
        finally:
            profile.account_file = _real_account_file
    run.__name__ = fn.__name__
    return run


@isolated
def test_matching_address_is_ok():
    account(["203.0.113.10"])
    state, _ = network.check(lookup=lambda: "203.0.113.10")
    assert state == "ok"


@isolated
def test_backup_address_counts():
    account(["203.0.113.10", "198.51.100.20"])
    assert network.check(lookup=lambda: "198.51.100.20")[0] == "ok"


@isolated
def test_different_address_says_both_and_what_to_do():
    account(["203.0.113.10"])
    state, message = network.check(lookup=lambda: "192.0.2.30")
    assert state == "mismatch"
    assert "192.0.2.30" in message and "203.0.113.10" in message, message
    assert "Api Key Generation" in message, message


@isolated
def test_failed_lookup_is_not_called_a_mismatch():
    account(["203.0.113.10"])
    assert network.check(lookup=lambda: None)[0] == "unknown"


@isolated
def test_nothing_saved_says_so():
    account()
    assert network.check(lookup=lambda: "1.2.3.4")[0] == "unset"


@isolated
def test_ipv6_is_compared_as_an_address_not_as_text():
    account(["2001:DB8:0:0::1"])
    assert network.check(lookup=lambda: "2001:db8::1")[0] == "ok"


@isolated
def test_only_addresses_are_saved_and_privately():
    path = account(["203.0.113.10", ""])
    assert json.loads(path.read_text()) == {"registered_ips": ["203.0.113.10"]}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for bad in (["not-an-ip"], ["122.161.64"], []):
        try:
            network.save(bad)
        except ValueError:
            continue
        raise AssertionError(f"saved {bad!r}")


def test_ip_refusals_are_recognised_and_nothing_else():
    for said in ("Invalid IP address", "IP not whitelisted",
                 "Request not allowed from this IP", "ip address mismatch"):
        assert network.explain(said), said
    for said in ("Insufficient margin", "Session Expired :  Invalid Session Key",
                 "RMS:Margin Exceeds", "IPO not open", "Order price out of range",
                 None, ""):
        assert network.explain(said) is None, said


@isolated
def test_login_stops_before_the_browser_on_a_mismatch():
    account(["203.0.113.10"])
    import builtins
    from shoonya import login, credentials
    asked = []
    saved = (network.current, builtins.input, credentials.ask)
    network.current = lambda timeout=3: "192.0.2.30"
    builtins.input = lambda prompt="": "n"
    credentials.ask = lambda: asked.append(1) or {}
    try:
        login.main()
    except SystemExit:
        pass
    else:
        raise AssertionError("carried on to the login")
    finally:
        network.current, builtins.input, credentials.ask = saved
    assert not asked, "asked for credentials despite the wrong address"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            passed += 1
            print(f"ok   {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
