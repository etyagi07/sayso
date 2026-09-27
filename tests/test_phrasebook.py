"""The phrasebook: what Sayso does with each thing you might say.

Read this file to know how it behaves. Each line is a sentence and what
must happen. The rule behind it: every word in an order has a known job -
action, index, call or put, a company, a number in a clear role, intraday
or delivery, or harmless filler. A sentence with a word that has no job is
asked about, never guessed at, because a guess here is a real trade.

Runs offline.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.parser import parse  # noqa: E402
from voice import stocks  # noqa: E402

TRADES = {"option_buy", "option_exit", "order"}

# sentence -> the fields the parse must have. Only listed fields are checked.
WORKS = [
    # --- options: buy ------------------------------------------------------
    ("buy nifty call", {"intent": "option_buy", "option_type": "CE",
                        "underlying": "NIFTY"}),
    ("Buy two lots of Nifty 23100 call.",
     {"intent": "option_buy", "number_spans": [["two"], ["23100"]]}),
    ("buy 2 nifty calls", {"intent": "option_buy", "number_spans": [["2"]]}),
    ("buy nifty call 2 lots", {"intent": "option_buy",
                               "number_spans": [["2"]]}),
    ("buy three lots bank nifty call",
     {"intent": "option_buy", "underlying": "BANKNIFTY",
      "number_spans": [["three"]]}),
    ("Buy Sensex call, 81000.", {"intent": "option_buy",
                                 "strike_spans": [["81000"]]}),
    ("buy nifty call twenty five thousand",
     {"intent": "option_buy",
      "strike_spans": [["twenty", "five", "thousand"]]}),
    ("buy nifty twenty-five one-hundred call",
     {"intent": "option_buy",
      "number_spans": [["twenty", "five", "one", "hundred"]]}),
    ("buy nifty weekly atm call", {"intent": "option_buy", "weekly": True,
                                   "atm": True}),
    ("buy nifty call at the money", {"intent": "option_buy", "atm": True}),
    ("buy nifty call strike 23100", {"intent": "option_buy",
                                     "strike_spans": [["23100"]]}),
    ("Okay, buy a Nifty put.", {"intent": "option_buy", "option_type": "PE"}),
    ("By Nifty call.", {"intent": "option_buy"}),
    ("Buy a Nifty call. Thank you.", {"intent": "option_buy"}),
    # the exact sentence from the live trade on 25 September
    ("Hm. Buy call two three one five zero.",
     {"intent": "option_buy", "strike_spans": [["two", "three", "one", "five",
                                                "zero"]]}),
    ("buy, um, nifty call, uh, 2 lots", {"intent": "option_buy",
                                         "number_spans": [["2"]]}),
    # --- options: exit -----------------------------------------------------
    ("exit call", {"intent": "option_exit", "option_type": "CE"}),
    ("exit my nifty call", {"intent": "option_exit", "underlying": "NIFTY"}),
    ("square off my nifty put", {"intent": "option_exit",
                                 "option_type": "PE"}),
    ("close the sensex call", {"intent": "option_exit",
                               "underlying": "SENSEX"}),
    ("book profit on my nifty call", {"intent": "option_exit"}),
    ("get me out of my nifty put", {"intent": "option_exit"}),
    ("exit one lot of the 23100 call",
     {"intent": "option_exit", "number_spans": [["one"], ["23100"]]}),
    ("close half my nifty call", {"intent": "option_exit", "half": True}),
    # --- corrections: only a like-for-like swap ------------------------------
    ("buy nifty call no wait put", {"intent": "option_buy",
                                    "option_type": "PE"}),
    ("buy a call not a put on nifty", {"intent": "option_buy",
                                       "option_type": "CE"}),
    ("buy nifty call instead of put", {"intent": "option_buy",
                                       "option_type": "CE"}),
    ("buy nifty 25200 call, make it two lots",
     {"intent": "option_buy", "number_spans": [["25200"]],
      "lots_override": ["two"]}),
    ("buy two nifty 25200 calls, sorry, 25300",
     {"intent": "option_buy", "number_spans": [["two"], ["25200"]],
      "strike_override": ["25300"]}),
    # --- stocks ------------------------------------------------------------
    ("Buy 1 Yes Bank intraday.", {"intent": "order", "side": "B",
                                  "quantity": 1, "name": "yesbank",
                                  "product": "I"}),
    ("Sell my Yes Bank.", {"intent": "order", "side": "S",
                           "quantity": None}),
    ("Sell 10 Infosys. Bye.", {"intent": "order", "side": "S",
                               "quantity": 10}),
    ("By 10 Yes Bank", {"intent": "order", "side": "B"}),
    ("sell yes bank 500", {"intent": "order", "quantity": 500}),
    ("buy two hundred fifty yes bank", {"intent": "order", "quantity": 250}),
    ("buy 10 yes bank at market", {"intent": "order", "quantity": 10,
                                   "name": "yesbank"}),
    ("buy 10 yes bank now", {"intent": "order", "name": "yesbank"}),
    ("buy yes bank at 22 rupees", {"intent": "order", "price": 22}),
    ("sell half my yes bank", {"intent": "order", "side": "S",
                               "half": True}),
    ("exit all yes bank", {"intent": "order", "side": "S",
                           "name": "yesbank"}),
    ("sell all infosys", {"intent": "order", "side": "S",
                          "name": "infosys"}),
    ("book profit in infosys", {"intent": "order", "side": "S",
                                "name": "infosys"}),
    ("buy 10 coal india intraday", {"intent": "order", "name": "coal india"}),
    # --- information -----------------------------------------------------------
    ("what is yes bank at", {"intent": "quote", "name": "yesbank"}),
    ("what's the price of yes bank", {"intent": "quote",
                                      "name": "yesbank"}),
    ("what is the nifty 23100 call at", {"intent": "option_quote"}),
    ("funds", {"intent": "funds"}),
    ("what are my positions", {"intent": "positions"}),
]

# Sentences that must NOT become a trade. What they do instead is shown
# where it matters; the rest just must not trade.
REFUSED = [
    # words with no job
    ("buy nifty call stop loss 5", "not_followed"),
    ("buy nifty call 7 october", "not_followed"),
    ("buy nifty call next expiry", "not_followed"),
    ("buy nifty monthly call", "not_followed"),
    ("buy nifty call out of the money", "not_followed"),
    ("buy half lot nifty call", "not_followed"),
    ("sell yes bank at 22 100", "not_followed"),
    # rupee amounts
    ("buy yes bank worth 500 intraday", "rupee_amount"),
    ("buy 500 rupees of yes bank", "rupee_amount"),
    ("buy nifty call 8 rupees", "rupee_amount"),
    ("buy nifty call ₹8", "rupee_amount"),
    # questions, including about the past
    ("is my nifty call closed?", "question"),
    ("did I buy a nifty call today?", "question"),
    ("how many nifty calls did I buy", "question"),
    ("is it time to buy nifty call", "question"),
    ("what happens if I buy a nifty call", "question"),
    ("Okay. Should I buy a Nifty call?", "question"),
    ("when did I exit nifty call", "question"),
    # saying not to
    ("I won't buy nifty call", "negated"),
    ("we shouldn't buy nifty call", "negated"),
    ("I can't buy nifty call", "negated"),
    ("Don’t buy a Nifty call", "negated"),
    ("cancel buy nifty call", "negated"),
    ("stop buy nifty call", "negated"),
    # bare "no" - "call, no put" can mean either
    ("buy nifty call no puts", "unclear_correction"),
    ("buy nifty put no call", "unclear_correction"),
    ("buy nifty call and no put", "unclear_correction"),
    # numbers that can't be read safely
    ("buy two fifty yes bank", "number_unclear"),
    ("buy one zero zero yes bank", "number_unclear"),
    ("buy nifty 23100s call", "number_unclear"),
    # "bye" and "by" that aren't buy
    ("Bye. Nifty call.", None),
    ("By the way, nifty call is up", None),
    # everything else found in review
    ("buy yes bank sell infosys", "side_ambiguous"),
    ("buy nifty call on sensex", "index_ambiguous"),
    ("sell 10 yes bank 20", "quantity_ambiguous"),
    ("buy nifty call no wait sell", "unclear_correction"),
]


def test_things_that_work():
    bad = []
    for said, want in WORKS:
        got = parse(said)
        wrong = {k: got.get(k) for k, v in want.items() if got.get(k) != v}
        if wrong:
            bad.append(f"{said!r}: wanted {want}, got {got}")
    assert not bad, "\n  " + "\n  ".join(bad)


def test_things_that_must_not_trade():
    bad = []
    for said, want in REFUSED:
        got = parse(said)
        if got["intent"] in TRADES:
            bad.append(f"{said!r} TRADES: {got}")
        elif want and got["intent"] != want:
            bad.append(f"{said!r}: wanted {want}, got {got['intent']}")
    assert not bad, "\n  " + "\n  ".join(bad)


def test_company_names_are_matched_not_guessed():
    # A near-miss is not a match: "idfc bank" is not HDFC Bank.
    assert stocks.resolve("idfc bank") is None
    # A partial name is confirmed, even when only one company fits.
    for partial in ("bharat", "hindustan", "power"):
        found = stocks.resolve(partial)
        assert found is None or "ambiguous" in found, (partial, found)
    assert stocks.resolve("infosys")["symbol"] == "INFY"


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
