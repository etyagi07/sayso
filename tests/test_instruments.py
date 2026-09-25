"""Contract selection, without the network or the real symbol masters."""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shoonya import instruments as ins  # noqa: E402

THU = date(2026, 10, 1)      # a SENSEX expiry day
NEXT = date(2026, 10, 8)


def fake(name):
    rows = []
    for exp in (THU, NEXT):
        for k in (73800, 73900, 74000):
            for ot in ("CE", "PE"):
                rows.append({"tsym": f"S{exp:%d}{k}{ot}", "token": "1",
                             "lot": 20, "tick": 0.05, "strike": k,
                             "option_type": ot, "expiry": exp,
                             "exchange": "BFO", "underlying": name})
    return rows


def at(hh, mm):
    return datetime(2026, 10, 1, hh, mm, tzinfo=ins.IST)


def with_fake(fn):
    def run():
        real = ins.contracts
        ins.contracts = fake
        try:
            fn()
        finally:
            ins.contracts = real
    run.__name__ = fn.__name__
    return run


@with_fake
def test_expiring_contract_trades_until_close():
    assert ins.expiries("SENSEX", now=at(10, 0))[0] == THU
    assert ins.expiries("SENSEX", now=at(15, 29))[0] == THU


@with_fake
def test_rolls_to_next_expiry_after_close():
    # After 15:30 the day's contract has expired; picking it would target
    # something that can no longer be traded.
    assert ins.expiries("SENSEX", now=at(15, 30))[0] == NEXT
    assert ins.expiries("SENSEX", now=at(18, 0))[0] == NEXT


def test_atm_uses_each_index_spacing():
    assert ins.atm_strike(23140, "NIFTY") == 23150     # steps of 50
    assert ins.atm_strike(55580, "BANKNIFTY") == 55600  # steps of 100
    assert ins.atm_strike(73896, "SENSEX") == 73900


@with_fake
def test_unlisted_strike_uses_nearest_and_says_so():
    c = ins.find("SENSEX", "CE", strike=73950, expiry=THU)
    assert c["strike"] in (73900, 74000)
    assert c["strike_adjusted_from"] == 73950


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
