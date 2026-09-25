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
    "shoonya.credentials",
    "voice.agent", "voice.parser", "voice.strikes", "voice.numbers",
    "voice.fuzzy", "voice.safety", "voice.config", "voice.listen",
    "voice.calibrate", "voice.doctor", "voice.cli", "voice.main",
]

# Names other code calls by hand, so a rename or deletion is caught here.
PUBLIC = {
    "shoonya.credentials": ["prompt", "ensure", "have_credentials",
                            "read_env_file", "write_env_file"],
    "shoonya.broker": ["order", "option_contract", "marketable_price",
                       "quote_checked", "place_direct_and_confirm",
                       "positions", "funds", "quote", "resolve_symbol"],
    "voice.parser": ["parse"],
    "voice.strikes": ["resolve", "read_order", "number_spans"],
    "voice.safety": ["check", "check_option", "record", "status"],
    "voice.agent": ["handle"],
    "voice.config": ["load", "save", "backend", "is_calibrated"],
}


def test_modules_import():
    for name in MODULES:
        importlib.import_module(name)


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
