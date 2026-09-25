"""Spoken strike and quantity parsing.

Fixed ladder and spot, so these run without market access. The cases that
matter most are the ones that must NOT resolve: a wrong strike here is a
real trade in the wrong instrument, and it looks perfectly valid on screen.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.strikes import read_order, resolve  # noqa: E402

SPOT = 23047
LADDER = set(range(22500, 23700, 50))


def r(said):
    return resolve(said.split(), LADDER, SPOT)[0]


def o(said):
    return read_order([said.split()], LADDER, SPOT)


def test_digits():
    assert r("23100") == 23100
    assert r("23050") == 23050
    assert r("22950") == 22950


def test_trader_shorthand():
    assert r("twenty three fifty") == 23050
    assert r("twenty three one hundred") == 23100
    assert r("twenty three two hundred") == 23200
    assert r("twenty two nine fifty") == 22950
    assert r("twenty three thousand") == 23000
    assert r("twenty three thousand one hundred") == 23100


def test_refuses_unlisted_strike():
    # Strikes run in 50s - 23075 does not exist and must not be rounded to.
    assert r("23075") is None
    assert r("23010") is None


def test_refuses_far_from_spot():
    assert r("19000") is None


def test_refuses_genuine_ambiguity():
    # "twenty three hundred" is 23,000 or 23,100 depending on convention.
    # Both are listed and near spot, so it must ask rather than pick.
    strike, why = resolve("twenty three hundred".split(), LADDER, SPOT)
    assert strike is None
    assert "could be" in why


def test_quantity_alone():
    assert o("2") == (2, None, None)
    assert o("one") == (1, None, None)


def test_quantity_and_strike_together():
    # Regression: "two 23100" once yielded strike 23300 via 2*100+23100,
    # which is a real strike near spot and so passed validation silently.
    assert o("two 23100") == (2, 23100, None)
    assert o("2 23100") == (2, 23100, None)
    assert o("two twenty three one hundred") == (2, 23100, None)
    assert o("three twenty two nine fifty") == (3, 22950, None)


def test_separate_spans():
    lots, strike, err = read_order([["2"], ["23100"]], LADDER, SPOT)
    assert (lots, strike, err) == (2, 23100, None)


def test_conflicting_input_is_refused():
    _, _, err = read_order([["23100"], ["23200"]], LADDER, SPOT)
    assert err and "two strikes" in err


def test_strike_read_out_digit_by_digit():
    # People read strikes out as digits: "two three five zero" is 23050.
    assert r("two three five zero") == 23050
    assert r("two three one zero zero") == 23100
    assert r("two two nine five zero") == 22950
    assert r("2350") == 23050


def test_digit_run_is_never_summed_into_a_quantity():
    # Regression, and the worst bug found so far: "buy call for two three
    # five zero" summed 2+3+5+0 to 10, ordered 10 lots at the money, and
    # built an 82,972 rupee order from what was meant to be a strike.
    lots, strike, err = read_order([["two", "three", "five", "zero"]],
                                   LADDER, SPOT)
    assert lots is None, f"digit run became {lots} lots"
    assert strike == 23050

    # An unreadable long run must refuse, not fall back to a lot count.
    lots, strike, err = read_order([["nine", "nine", "nine", "nine"]],
                                   LADDER, SPOT)
    assert lots is None and strike is None and err


def test_short_runs_are_still_quantities():
    assert o("two") == (2, None, None)
    assert o("ten") == (10, None, None)


def test_no_numbers():
    assert read_order([], LADDER, SPOT) == (None, None, None)


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
