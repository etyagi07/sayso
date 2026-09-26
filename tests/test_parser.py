"""The parser, against things people actually say.

Every case here was a wrong trade found in review: a sell read as a buy, a
correction that kept the thing corrected, a quantity dropped so the whole
holding was sold. Each is kept so it cannot come back.

Runs offline.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.parser import parse  # noqa: E402
from voice import stocks  # noqa: E402


def intent(text):
    return parse(text)["intent"]


# --- sell must never become buy --------------------------------------------

def test_trailing_bye_does_not_turn_a_sell_into_a_buy():
    # Whisper tacks "Bye." on the end of short clips.
    r = parse("Sell 10 Infosys. Bye.")
    assert r["intent"] == "order" and r["side"] == "S", r
    r = parse("Sell my Yes Bank. Bye.")
    assert r["intent"] == "order" and r["side"] == "S", r


def test_by_at_the_start_is_still_buy():
    r = parse("By 10 Yes Bank")
    assert r["intent"] == "order" and r["side"] == "B", r


def test_take_profit_is_an_exit_not_a_buy():
    r = parse("take profit on my nifty call")
    assert r["intent"] == "option_exit", r


def test_get_me_out_is_an_exit():
    r = parse("get me out of my nifty put")
    assert r["intent"] == "option_exit", r


def test_get_and_take_alone_are_not_buy():
    assert intent("take 10 yes bank") == "unknown"
    assert intent("get 10 yes bank") == "unknown"


def test_buy_and_sell_together_is_not_a_trade():
    r = parse("buy yes bank sell infosys")
    assert r["intent"] == "side_ambiguous", r


# --- corrections -----------------------------------------------------------

def test_x_instead_of_y_means_x():
    r = parse("buy nifty call instead of put")
    assert r["intent"] == "option_buy" and r["option_type"] == "CE", r
    r = parse("buy a put instead of a call on nifty")
    assert r["intent"] == "option_buy" and r["option_type"] == "PE", r


def test_changing_side_mid_sentence_does_not_keep_the_old_side():
    r = parse("buy nifty call no wait sell")
    assert r["intent"] != "option_buy", r


def test_no_dont_calls_it_off():
    r = parse("buy nifty call, no, don't")
    assert r["intent"] not in ("option_buy", "order"), r


def test_correction_of_type_still_works():
    r = parse("buy nifty call no wait put")
    assert r["intent"] == "option_buy" and r["option_type"] == "PE", r


def test_equity_correction_is_not_guessed():
    r = parse("sell 10 yes bank sorry 20")
    assert r["intent"] != "order" or r["quantity"] == 20, r


# --- quantity said after the name ---------------------------------------------

def test_trailing_quantity_is_read():
    r = parse("sell yes bank 500")
    assert r["intent"] == "order" and r["quantity"] == 500, r
    r = parse("buy infosys 10 for the day")
    assert r["intent"] == "order" and r["quantity"] == 10, r


def test_two_quantities_is_not_a_trade():
    r = parse("sell 10 yes bank 20")
    assert r["intent"] != "order", r


# --- names --------------------------------------------------------------------

def test_for_delivery_leaves_no_word_behind():
    r = parse("buy 10 hdfc bank for delivery")
    assert r["name"] == "hdfc bank" and r["product"] == "C", r
    r = parse("buy 10 hdfc bank as intraday")
    assert r["name"] == "hdfc bank" and r["product"] == "I", r


def test_unknown_company_is_not_trimmed_into_a_known_one():
    for name, wrong in (("sbi card", "SBIN"), ("sun tv", "SUNPHARMA"),
                        ("bharat forge", "BEL"),
                        ("reliance power", "RELIANCE")):
        found = stocks.resolve(name)
        assert found is None or "ambiguous" in found, (name, found, wrong)


def test_nifty_bank_is_bank_nifty():
    r = parse("buy nifty bank call")
    assert r["intent"] == "option_buy" and r["underlying"] == "BANKNIFTY", r


def test_two_indices_is_asked_about():
    r = parse("buy nifty call on sensex")
    assert r["intent"] == "index_ambiguous", r


def test_coal_india_is_a_stock_not_a_call():
    r = parse("buy 10 coal india")
    assert r["intent"] == "order" and r["name"] == "coal india", r


# --- questions and negation -------------------------------------------------------

def test_questions_after_filler_are_questions():
    for text in ("Okay. Should I buy a Nifty call?",
                 "what call should I buy",
                 "how many nifty calls can I buy",
                 "so do you think I should sell yes bank"):
        assert intent(text) == "question", text


def test_quotes_are_still_quotes():
    assert intent("what's yes bank at") == "quote"
    assert intent("price of infosys") == "quote"


def test_curly_apostrophe_negation():
    assert intent("Don’t buy a Nifty call") == "negated"


# --- numbers -----------------------------------------------------------------------

def test_at_price_is_not_a_lot_count():
    r = parse("buy nifty call at 85")
    assert r["intent"] == "option_buy", r
    assert r.get("price_spans") == [["85"]], r
    assert r["number_spans"] == [], r


def test_comma_in_a_strike_is_kept():
    r = parse("buy nifty 23,000 call")
    assert r["number_spans"] == [["23000"]], r


def test_unreadable_number_is_not_ignored():
    # Silently dropping it would fall back to the at-the-money strike.
    r = parse("buy nifty 23100s call")
    assert r["intent"] == "number_unclear", r


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
