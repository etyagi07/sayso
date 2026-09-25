"""Every module imports cleanly.

Catches what a syntax check cannot: an edit that deletes a `def` line
leaves the body orphaned after a `return`, which parses fine and only
fails when something tries to use the missing name.
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODULES = [
    "shoonya.client", "shoonya.broker", "shoonya.instruments",
    "shoonya.credentials", "shoonya.underlyings",
    "voice.agent", "voice.parser", "voice.strikes", "voice.numbers",
    "voice.fuzzy", "voice.safety", "voice.config", "voice.listen",
    "voice.calibrate", "voice.doctor", "voice.cli", "voice.main",
    "voice.stocks", "voice.speak", "voice.watch",
]

# Names other code calls by hand, so a rename or deletion is caught here.
PUBLIC = {
    "shoonya.credentials": ["prompt", "ensure", "have_credentials",
                            "read_env_file", "write_env_file"],
    "shoonya.broker": ["place", "wait_for_outcome", "order_book",
                       "option_contract", "marketable_price",
                       "quote_checked", "positions", "funds",
                       "BrokerError"],
    "voice.stocks": ["resolve", "add", "remove", "all_stocks", "symbols"],
    "voice.parser": ["parse"],
    "voice.strikes": ["resolve", "read_order", "number_spans"],
    "voice.safety": ["check", "check_option", "record", "status"],
    "voice.agent": ["handle", "_execute", "friendly"],
    "shoonya.instruments": ["contracts", "contract_for", "expiries", "find",
                            "ladder", "atm_strike"],
    "shoonya.underlyings": ["UNDERLYINGS", "UNSUPPORTED", "get", "band"],
    "voice.config": ["load", "save", "backend", "is_calibrated"],
}


def test_modules_import():
    for name in MODULES:
        importlib.import_module(name)


def test_sdk_calls_have_a_timeout():
    # Regression: none of the SDK's network calls set a timeout, so one
    # stalled connection froze a live session.
    import NorenRestApiPy.NorenApi as sdk
    import shoonya.client  # noqa: F401 - installs the timeout
    seen = {}
    real = sdk.requests.__class__.__getattr__
    import requests
    original = requests.post
    requests.post = lambda *a, **k: seen.update(k) or (_ for _ in ()).throw(
        requests.ConnectionError("stop"))
    try:
        try:
            sdk.requests.post("http://example.invalid", data="x")
        except requests.ConnectionError:
            pass
    finally:
        requests.post = original
    assert seen.get("timeout"), "SDK request sent without a timeout"


def test_prefix_search_is_gone():
    # The broker's prefix search turned "idea" into IDEAFORGE. Stock names
    # are matched against voice.stocks only; the search must not come back.
    import shoonya.broker as b
    assert not hasattr(b, "resolve_symbol")


def test_public_names_exist():
    for module, names in PUBLIC.items():
        mod = importlib.import_module(module)
        for name in names:
            assert hasattr(mod, name), f"{module}.{name} is missing"


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
