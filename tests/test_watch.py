"""The background fill watcher says what happened, truthfully.

Runs offline, against a stubbed order book.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shoonya.broker as b  # noqa: E402
from voice import watch  # noqa: E402


def told(state):
    said = []
    saved = (b.wait_for_outcome, watch._tell, watch.time.sleep)
    b.wait_for_outcome = lambda order_no, **k: state
    watch._tell = lambda text, outcome: said.append((text, outcome))
    watch.time.sleep = lambda s: None
    try:
        watch._watch("ORD1", "Your order", 0, 60)
    finally:
        b.wait_for_outcome, watch._tell, watch.time.sleep = saved
    return said[-1]


def test_partial_fill_then_cancel_is_not_a_rejection():
    text, outcome = told({"status": "CANCELED", "filled": 65, "quantity": 130,
                          "avg_fill_price": 80.5})
    assert outcome == "partial", (text, outcome)
    assert "65 of 130" in text and "80.50" in text, text


def test_missing_fill_price_is_not_read_as_none():
    text, _ = told({"status": "COMPLETE", "filled": 1, "quantity": 1,
                    "avg_fill_price": None})
    assert "None" not in text, text


def test_plain_cancel_says_cancelled():
    text, outcome = told({"status": "CANCELED", "filled": 0, "quantity": 1})
    assert outcome == "rejected" and "cancelled" in text, text


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
