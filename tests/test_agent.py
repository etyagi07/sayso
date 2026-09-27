"""The agent layer, against a fake broker.

This is where the bugs that matter live: being told an order is pending when
none was sent, a confirmed symbol being swapped for another, an edited price
being ignored, an expired session reading as an empty account. Each test
below is one of those, found in review and kept here so it cannot return.

Runs offline - nothing here touches the network or the real daily counters.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shoonya.broker as b  # noqa: E402
from datetime import date  # noqa: E402
from shoonya import instruments as ins  # noqa: E402
from voice import agent, safety  # noqa: E402

# A tiny offline stand-in for the symbol masters. Note the SENSEX symbol:
# BSE puts CE/PE at the end, so anything pattern-matching NIFTY's layout
# would never recognise it.
CONTRACTS = {
    "NIFTY29SEP26C23100": ("NIFTY", "CE", 23100, "NFO", "73906"),
    "NIFTY29SEP26C23150": ("NIFTY", "CE", 23150, "NFO", "73908"),
    "NIFTY29SEP26P23100": ("NIFTY", "PE", 23100, "NFO", "73907"),
    "BANKNIFTY29SEP26C55600": ("BANKNIFTY", "CE", 55600, "NFO", "69779"),
    "SENSEX26O0173900CE": ("SENSEX", "CE", 73900, "BFO", "886639"),
    "NIFTY29SEP26C24000": ("NIFTY", "CE", 24000, "NFO", "73999"),
    "NIFTY06OCT26C23100": ("NIFTY", "CE", 23100, "NFO", "74100",
                           date(2026, 10, 6)),
}


def fake_contract_for(tsym):
    if tsym not in CONTRACTS:
        return None
    u, ot, k, exch, tok, *expiry = CONTRACTS[tsym]
    return {"tsym": tsym, "underlying": u, "option_type": ot, "strike": k,
            "exchange": exch, "token": tok, "lot": 65,
            "expiry": expiry[0] if expiry else date(2026, 9, 29)}


def pos(tsym, qty, avg=100.0):
    return {"symbol": tsym, "qty": qty, "avg_price": avg, "ltp": avg + 1,
            "prd": "M"}

# Captured before any test swaps it for a fake.
REAL_POSITIONS = b.positions

YES = lambda p: True  # noqa: E731
NO = lambda p: False  # noqa: E731

QUOTE = {"stat": "Ok", "lp": "22.44", "bp1": "22.43", "sp1": "22.45",
         "ti": "0.01", "lc": "20.00", "uc": "25.00", "tsym": "YESBANK-EQ",
         "token": "11915"}


class Fake:
    """Replaces the broker functions the agent calls, and records sends."""

    def __init__(self, **over):
        self.sent = []
        self.saved = {}
        self.over = over

    def __enter__(self):
        state = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.saved["state"] = (safety.STATE_FILE, dict(safety._spent_today))
        safety.STATE_FILE = Path(state.name)
        safety._spent_today.clear()
        safety._spent_today.update(safety._blank())

        defaults = {
            "quote_checked": lambda *a, **k: dict(QUOTE),
            "positions": lambda include_closed=False: [],
            "place": self._place,
            "wait_for_outcome": lambda order_no, **k: {
                "order_no": order_no, "status": "COMPLETE", "final": True,
                "quantity": 1, "filled": 1, "avg_fill_price": 22.45},
        }
        defaults.update(self.over)
        for name, fn in defaults.items():
            self.saved[name] = getattr(b, name)
            setattr(b, name, fn)
        self.saved["_contract_for"] = ins.contract_for
        ins.contract_for = fake_contract_for
        agent._pending = None
        return self

    def _place(self, side, tsym, quantity, price, exchange, product):
        self.sent.append({"side": side, "tsym": tsym, "qty": quantity,
                          "price": price, "exchange": exchange,
                          "product": product})
        return {"status": "ACCEPTED", "order_no": "ORD1", "tag": "t"}

    def __exit__(self, *exc):
        ins.contract_for = self.saved.pop("_contract_for")
        agent._pending = None
        path, counters = self.saved.pop("state")
        safety.STATE_FILE = path
        safety._spent_today.clear()
        safety._spent_today.update(counters)
        for name, fn in self.saved.items():
            setattr(b, name, fn)


def orders_counted():
    return safety.status()["orders_today"]


# --- being told the truth ------------------------------------------------

def test_rejection_is_reported_as_rejection():
    # Regression: an error after confirmation was reported as "Order is
    # pending, not filled yet" while nothing had been sent.
    with Fake(place=lambda *a: {"status": "REJECTED",
                                "reason": "Insufficient margin"}):
        r = agent.handle("buy one yesbank intraday", YES)
        assert r["outcome"] == "rejected"
        assert "pending" not in r["speak"].lower()
        assert "Insufficient margin" in r["speak"]
        assert orders_counted() == 0


def test_lost_reply_is_not_called_a_rejection():
    # A dropped connection does not mean the broker refused. Saying
    # "rejected" invites a retry and a doubled position.
    with Fake(place=lambda *a: {"status": "UNKNOWN", "reason": "timeout"}):
        r = agent.handle("buy one yesbank intraday", YES)
        assert r["outcome"] == "unknown"
        assert "rejected" not in r["speak"].lower()
        assert "order book" in r["speak"].lower()
        assert orders_counted() == 1, "possibly live - must count"


def test_exchange_rejection_after_acceptance_is_not_counted():
    with Fake(wait_for_outcome=lambda n, **k: {
            "status": "REJECTED", "final": True, "reason": "Circuit limit"}):
        r = agent.handle("buy one yesbank intraday", YES)
        assert r["outcome"] == "rejected"
        assert "Circuit limit" in r["speak"]
        assert orders_counted() == 0


def test_resting_order_says_resting():
    with Fake(wait_for_outcome=lambda n, **k: {
            "status": "OPEN", "final": False, "quantity": 1, "filled": 0}):
        r = agent.handle("buy one yesbank intraday", YES)
        assert r["outcome"] == "resting"
        assert "not filled" in r["speak"]


def test_partial_fill_says_partial():
    with Fake(wait_for_outcome=lambda n, **k: {
            "status": "OPEN", "final": False, "quantity": 4, "filled": 1,
            "avg_fill_price": 22.45}):
        r = agent.handle("buy four yesbank intraday", YES)
        assert r["outcome"] == "partial"
        assert "1 of 4" in r["speak"]


def broker_says(reply):
    """The real position book, with the broker's HTTP reply stubbed."""
    return {"positions": REAL_POSITIONS, "_ids": lambda: ("U1", "U1"),
            "_raw_post": lambda path, values: dict(reply)}


def test_expired_session_is_not_an_empty_account():
    # Regression: the broker answers "no data" and "session expired" with
    # the same status, and both used to read as "you have no positions".
    # Goes through the real book reader - that is where they are told apart.
    expired = {"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Session Key"}
    with Fake(**broker_says(expired)):
        for said in ("what do i own", "exit call", "sell my yesbank"):
            r = agent.handle(said, YES)
            assert r.get("broker_error"), said
            assert "no open" not in r["speak"].lower(), said
            assert "don't hold" not in r["speak"].lower(), said
    # ...while a genuinely empty book still reads as empty.
    with Fake(**broker_says({"stat": "Not_Ok", "emsg": "no data"})):
        r = agent.handle("what do i own", YES)
        assert r["speak"] == "You have no open positions.", r["speak"]


# --- sending what was confirmed ------------------------------------------

def test_confirmed_symbol_is_the_one_sent():
    # Regression: after confirmation the symbol was searched for again, and
    # names were found by prefix search - which is how "idea" became
    # IDEAFORGE. Stocks now come from a known list; the broker is never
    # searched, and what was confirmed is exactly what is sent.
    calls = []
    shown = {}
    with Fake(_raw_post=lambda path, values: calls.append(path) or {}) as f:
        agent.handle("buy one yesbank intraday",
                     lambda p: (shown.update(p), True)[1])
        assert "/SearchScrip" not in calls, "searched the broker for a name"
        assert f.sent[0]["tsym"] == shown["symbol"] == "YESBANK-EQ"


def test_edited_price_is_sent_on_exit():
    # Regression: pressing p on an exit changed the screen, not the order.
    held = [{"symbol": "NIFTY29SEP26C23100", "qty": 65, "avg_price": 100.0,
             "ltp": 101.0, "prd": "M"}]
    opt_quote = {"stat": "Ok", "lp": "101.00", "bp1": "100.90",
                 "sp1": "101.10", "ti": "0.05", "lc": "0.05", "uc": "500",
                 "tsym": "NIFTY29SEP26C23100", "token": "73906"}

    def edit(preview):
        preview["price"] = 105.00
        return True

    with Fake(positions=lambda include_closed=False: held,
              quote_checked=lambda *a, **k: dict(opt_quote)) as f:
        agent.handle("exit call", edit)
        assert f.sent, "nothing was sent"
        assert f.sent[0]["price"] == 105.00, f.sent[0]


def test_edited_price_is_sent_on_buys():
    # The exit case above had a test; buys did not.
    def edit(preview):
        preview["price"] = 22.60
        return True
    with Fake() as f:
        agent.handle("buy one yesbank intraday", edit)
        assert f.sent and f.sent[0]["price"] == 22.60, f.sent

    def edit_option(preview):
        preview["price"] = 90.00
        return True
    saved = agent._ladder_and_spot
    agent._ladder_and_spot = lambda name: (LADDER, 23047.0, date(2026, 9, 29))
    try:
        with Fake(option_contract=fake_option_contract) as f:
            agent.handle("buy nifty call", edit_option)
            assert f.sent and f.sent[0]["price"] == 90.00, f.sent
    finally:
        agent._ladder_and_spot = saved


def test_unchecked_quote_never_prices_an_order():
    # Regression: the equity confirm box priced off a raw quote, which the
    # broker sometimes returns for the wrong instrument.
    with Fake(quote_checked=lambda *a, **k: None) as f:
        r = agent.handle("buy one yesbank intraday", YES)
        assert r.get("blocked")
        assert not f.sent


# --- exits are never trapped ---------------------------------------------

def test_equity_exit_is_not_blocked_by_the_size_cap():
    # Regression: a sell to close went through the order-size cap, so a
    # position could be impossible to exit by voice. 900 shares is over
    # the per-order value cap - smaller, and this proves nothing.
    held = [{"symbol": "YESBANK-EQ", "qty": 900, "avg_price": 22.0,
             "ltp": 22.44, "prd": "C"}]
    assert 900 * 22.4 > safety.LIMITS.max_order_value
    with Fake(positions=lambda include_closed=False: held) as f:
        r = agent.handle("sell my yesbank", YES)
        assert not r.get("blocked"), r["speak"]
        assert f.sent[0]["qty"] == 900
        assert f.sent[0]["side"] == "S"
        assert orders_counted() == 0, "exits do not use up the allowance"


def test_cannot_sell_more_than_held():
    held = [{"symbol": "YESBANK-EQ", "qty": 2, "avg_price": 22.0,
             "ltp": 22.44, "prd": "C"}]
    with Fake(positions=lambda include_closed=False: held) as f:
        r = agent.handle("sell five yesbank", YES)
        assert r.get("blocked")
        assert not f.sent


def test_exit_asks_when_two_positions_match():
    # Regression: with two calls open, "exit call" closed whichever came
    # first.
    held = [{"symbol": "NIFTY29SEP26C23100", "qty": 65, "avg_price": 100.0,
             "ltp": 101.0, "prd": "M"},
            {"symbol": "NIFTY29SEP26C23150", "qty": 130, "avg_price": 70.0,
             "ltp": 71.0, "prd": "M"}]
    with Fake(positions=lambda include_closed=False: held) as f:
        r = agent.handle("exit call", YES)
        assert r.get("needs_answer") == "strike", r["speak"]
        assert not f.sent


def test_declining_sends_nothing():
    with Fake() as f:
        r = agent.handle("buy one yesbank intraday", NO)
        assert r["speak"] == "Cancelled."
        assert not f.sent
        assert orders_counted() == 0


# --- indices ------------------------------------------------------------

def test_exit_recognises_a_sensex_position():
    # SENSEX symbols end in CE/PE. The old pattern-match only understood
    # NIFTY's layout and would have said "no open call position".
    held = [pos("SENSEX26O0173900CE", 20, 540.0)]
    with Fake(positions=lambda include_closed=False: held) as f:
        agent.handle("exit call", YES)
        assert f.sent and f.sent[0]["tsym"] == "SENSEX26O0173900CE"
        assert f.sent[0]["exchange"] == "BFO", f.sent[0]


def test_exit_across_indices_asks_which_index():
    held = [pos("NIFTY29SEP26C23100", 65), pos("SENSEX26O0173900CE", 20)]
    with Fake(positions=lambda include_closed=False: held) as f:
        r = agent.handle("exit call", YES)
        assert r.get("needs_answer") == "underlying", r["speak"]
        assert not f.sent
        # Answering with just the index finishes the exit.
        agent.handle("sensex", YES)
        assert f.sent and f.sent[0]["tsym"] == "SENSEX26O0173900CE"


def test_named_index_narrows_the_exit():
    held = [pos("NIFTY29SEP26C23100", 65), pos("BANKNIFTY29SEP26C55600", 30)]
    with Fake(positions=lambda include_closed=False: held) as f:
        agent.handle("exit the bank nifty call", YES)
        assert f.sent and f.sent[0]["tsym"] == "BANKNIFTY29SEP26C55600"


def test_named_strike_narrows_the_exit():
    held = [pos("NIFTY29SEP26C23100", 65), pos("NIFTY29SEP26C23150", 65)]
    with Fake(positions=lambda include_closed=False: held) as f:
        agent.handle("exit the 23150 call", YES)
        assert f.sent and f.sent[0]["tsym"] == "NIFTY29SEP26C23150"


def test_same_index_two_strikes_asks_which_strike():
    held = [pos("NIFTY29SEP26C23100", 65), pos("NIFTY29SEP26C23150", 65)]
    with Fake(positions=lambda include_closed=False: held) as f:
        r = agent.handle("exit nifty call", YES)
        assert r.get("needs_answer") == "strike", r["speak"]
        agent.handle("twenty three one hundred", YES)
        assert f.sent and f.sent[0]["tsym"] == "NIFTY29SEP26C23100"


def test_no_index_named_asks_before_anything_else():
    with Fake() as f:
        r = agent.handle("buy call", YES)
        assert r.get("needs_answer") == "underlying"
        assert not f.sent


def test_an_unrelated_command_drops_the_question():
    # A pending order must not be completed later by accident.
    with Fake() as f:
        agent.handle("buy call", YES)
        agent.handle("what are my limits", YES)
        r = agent.handle("bank nifty", YES)
        assert "wasn't waiting" in r["speak"]
        assert not f.sent


def test_pending_question_expires():
    with Fake() as f:
        agent.handle("buy call", YES)
        agent._pending["expires"] = 0
        r = agent.handle("sensex", YES)
        assert "wasn't waiting" in r["speak"]
        assert not f.sent


def test_unsupported_index_is_never_read_as_a_supported_one():
    # "fin nifty" contains "nifty" - it must not become a NIFTY order.
    with Fake() as f:
        for said in ("buy fin nifty call", "buy sensex fifty put",
                     "buy nifty next fifty call"):
            r = agent.handle(said, YES)
            assert "aren't supported" in r["speak"], said
        assert not f.sent


def test_index_without_call_or_put_is_refused():
    with Fake() as f:
        r = agent.handle("buy bank nifty", YES)
        assert "say call or put" in r["speak"]
        assert not f.sent


# --- how people actually talk ------------------------------------------

def test_mid_sentence_correction_is_what_gets_previewed():
    # Regression: "buy call no wait put" previewed a CALL - the thing just
    # cancelled - and a quick 'y' would have bought it.
    import voice.parser as parser
    for said, expected in (("buy nifty call no wait put", "PE"),
                           ("buy nifty put sorry call", "CE")):
        assert parser.parse(said)["option_type"] == expected, said


def test_things_that_are_not_instructions_do_nothing():
    with Fake() as f:
        for said in ("don't buy a call", "do not sell",
                     "should i buy yesbank", "what if i buy a put",
                     "what was yesterday's close on yesbank",
                     "buy call put", "cancel", "never mind"):
            r = agent.handle(said, YES)
            assert "preview" not in r, said
        assert not f.sent


# --- stocks ---------------------------------------------------------------

def test_asks_intraday_or_delivery_and_uses_the_answer():
    with Fake() as f:
        r = agent.handle("buy one yesbank", YES)
        assert r.get("needs_answer") == "product" and not f.sent
        agent.handle("delivery", YES)
        assert f.sent and f.sent[0]["product"] == "C"


def test_asks_how_many_and_uses_the_answer():
    with Fake() as f:
        r = agent.handle("buy yesbank intraday", YES)
        assert r.get("needs_answer") == "quantity" and not f.sent
        agent.handle("three", YES)
        assert f.sent and f.sent[0]["qty"] == 3 and f.sent[0]["product"] == "I"


def test_company_that_could_be_two_is_asked_about():
    with Fake() as f:
        for said in ("buy one hdfc intraday", "buy one tata intraday",
                     "buy one bajaj intraday"):
            r = agent.handle(said, YES)
            assert r.get("needs_clarification"), said
        assert not f.sent


def test_unknown_company_is_refused_not_searched():
    # The very first bug: "idea" became IDEAFORGE via prefix search.
    with Fake() as f:
        r = agent.handle("buy one idea intraday", YES)
        assert "don't know" in r["speak"] and not f.sent


def test_preview_names_the_company():
    shown = {}
    with Fake():
        agent.handle("buy one yesbank intraday",
                     lambda p: (shown.update(p), False)[1])
        assert shown["company"] == "Yes Bank"
        assert "intraday" in shown["say"]


def test_exit_keeps_the_positions_product():
    held = [{"symbol": "YESBANK-EQ", "qty": 5, "avg_price": 22.0,
             "ltp": 22.44, "prd": "I"}]
    with Fake(positions=lambda include_closed=False: held) as f:
        r = agent.handle("sell my yesbank", YES)
        assert r.get("needs_answer") is None, "asked intraday/delivery on an exit"
        assert f.sent[0]["product"] == "I"


# --- from the first real spoken session -----------------------------------

def test_a_repeated_answer_still_answers():
    # Whisper heard "Sensex" as "Sensex. Sensex." - the answer was not
    # recognised and the pending order was lost.
    import voice.parser as parser
    assert parser.parse("Sensex. Sensex.")["intent"] == "index_answer"
    assert parser.parse("intraday intraday")["intent"] == "product_answer"
    assert parser.parse("sensex nifty")["intent"] != "index_answer"


def test_a_misheard_at_in_a_quote_does_not_lose_the_company():
    # "at" came through as "Act": "what is hdfc life act". The quote rule
    # reads past it - the stock list does not guess by dropping words,
    # which turned "sbi card" into SBI and "sun tv" into Sun Pharma.
    from voice import stocks
    from voice.parser import parse
    assert parse("what is hdfc life act")["name"] == "hdfc life"
    assert stocks.resolve("hdfc life act") is None
    assert stocks.resolve("sbi card") is None



# --- found in the final review ---------------------------------------------

def test_unclear_speech_never_reaches_the_confirm_screen():
    shown = []
    with Fake() as f:
        for said in ("buy yes bank sell infosys", "buy nifty call on sensex",
                     "buy nifty call no wait sell", "sell 10 yes bank 20",
                     "buy nifty 23100s call", "buy nifty call, no, don't",
                     "Sell 10 Infosys. Buy.", "buy nifty call. no."):
            r = agent.handle(said, lambda p: shown.append(p) or True)
            assert r.get("blocked") or r.get("cancelled"), (said, r)
        assert not shown and not f.sent


LADDER = set(range(22500, 23700, 50))


def fake_option_contract(name, opt, strike=None, expiry=None):
    strike = strike or 23050
    return {"tsym": f"NIFTY29SEP26{opt[0]}{strike}", "token": "1",
            "exchange": "NFO", "underlying": name,
            "cadence": "monthly" if name == "BANKNIFTY" else "weekly",
            "lot": 65, "tick": 0.05, "strike": strike, "expiry": "2026-09-29",
            "expires_today": False, "option_type": opt, "ltp": 80.0,
            "bid": 79.9, "ask": 80.1, "spot": 23047.0, "lower_circuit": 1.0,
            "upper_circuit": 500.0}


def with_ladder(fn):
    saved = agent._ladder_and_spot
    agent._ladder_and_spot = lambda name: (LADDER, 23047.0, date(2026, 9, 29))
    try:
        with Fake(option_contract=fake_option_contract) as f:
            fn(f)
    finally:
        agent._ladder_and_spot = saved


def test_a_price_after_at_is_never_a_lot_count():
    # "buy nifty call at 85" was 85 lots.
    def check(f):
        r = agent.handle("buy nifty call at 85", YES)
        assert not f.sent, f.sent
        assert "market" in r["speak"], r["speak"]
    with_ladder(check)


def test_a_strike_after_at_is_still_the_strike():
    def check(f):
        agent.handle("buy nifty call at 23100", YES)
        assert f.sent and f.sent[0]["tsym"].endswith("C23100"), f.sent
        assert f.sent[0]["qty"] == 65, f.sent
    with_ladder(check)


def test_exit_never_closes_a_strike_that_was_not_said():
    # "twenty three one" summed to 24 and "hundred" read as thousands, so
    # this closed a 24000 call.
    held = [pos("NIFTY29SEP26C24000", 65)]
    with Fake(positions=lambda include_closed=False: held) as f:
        r = agent.handle("exit the twenty three one hundred call", YES)
        assert not f.sent, f.sent
        assert "don't hold" in r["speak"], r["speak"]


def test_exit_honours_a_spoken_quantity():
    held = [pos("NIFTY29SEP26C23100", 130)]            # two lots of 65
    with Fake(positions=lambda include_closed=False: held) as f:
        agent.handle("exit one lot of the 23100 call", YES)
        assert f.sent and f.sent[0]["qty"] == 65, f.sent
    with Fake(positions=lambda include_closed=False: held) as f:
        r = agent.handle("exit 3 lots of the nifty call", YES)
        assert not f.sent and "2 lots" in r["speak"], r["speak"]
    with Fake(positions=lambda include_closed=False: held) as f:
        agent.handle("exit the nifty call", YES)
        assert f.sent and f.sent[0]["qty"] == 130, f.sent


def test_same_strike_in_two_expiries_does_not_loop():
    held = [pos("NIFTY29SEP26C23100", 65), pos("NIFTY06OCT26C23100", 65)]
    with Fake(positions=lambda include_closed=False: held) as f:
        r = agent.handle("exit the 23100 call", YES)
        assert not f.sent
        assert r.get("needs_answer") != "strike", r["speak"]
        assert "expir" in r["speak"], r["speak"]


def test_partial_fill_then_cancel_is_not_called_a_rejection():
    with Fake(wait_for_outcome=lambda n, **k: {
            "status": "CANCELED", "final": True, "quantity": 2, "filled": 1,
            "avg_fill_price": 22.45, "reason": None}):
        r = agent.handle("buy 2 yesbank intraday", YES)
        assert r["outcome"] == "partial", r
        assert "rejected" not in r["speak"].lower(), r["speak"]
        assert "1 of 2" in r["speak"], r["speak"]
        assert orders_counted() == 1


def test_a_cancelled_order_is_called_cancelled():
    with Fake(wait_for_outcome=lambda n, **k: {
            "status": "CANCELED", "final": True, "quantity": 1, "filled": 0,
            "reason": None}):
        r = agent.handle("buy 1 yesbank intraday", YES)
        assert "rejected" not in r["speak"].lower(), r["speak"]
        assert "cancel" in r["speak"].lower(), r["speak"]
        assert orders_counted() == 0


def test_nothing_done_is_always_flagged():
    # The flag is what plays the "nothing sent" sound. Without it, a
    # misheard command was followed by silence - indistinguishable, eyes on
    # the chart, from an order still in flight.
    with Fake() as f:
        for said in ("the weather is nice", "sell my infosys", "exit call",
                     "buy yes bank sell infosys"):
            r = agent.handle(said, YES)
            assert r.get("blocked") or r.get("outcome"), (said, r)
        assert not f.sent


def test_an_ip_refusal_is_explained():
    # The broker's own words don't say what to do. Nothing was sent.
    with Fake(place=lambda *a: {"status": "REJECTED",
                                "reason": "Invalid IP address"}):
        r = agent.handle("buy one yesbank intraday", YES)
        assert "internet-address" in r["speak"], r["speak"]
        assert orders_counted() == 0

    def refused(include_closed=False):
        raise b.BrokerError("Request not allowed from this IP")
    with Fake(positions=refused):
        r = agent.handle("what do i own", YES)
        assert r.get("blocked") and "internet-address" in r["speak"], r


# --- every word has a job (the strict speech rule) ----------------------------

def test_a_number_that_is_not_a_strike_is_not_quietly_lots():
    def check(f):
        r = agent.handle("buy nifty call 5", YES)
        assert not f.sent and '"5 lots"' in r["speak"], r["speak"]
        r = agent.handle("buy nifty call for 5", YES)
        assert not f.sent and "price" in r["speak"], r["speak"]
        agent.handle("buy nifty call 2 lots", YES)
        assert f.sent and f.sent[0]["qty"] == 130, f.sent
    with_ladder(check)


def test_a_correction_swaps_only_what_was_corrected():
    def check(f):
        agent.handle("buy nifty 23100 call, make it two lots", YES)
        agent.handle("buy two nifty 23100 calls, sorry, 23150", YES)
        got = [(o["tsym"][-6:], o["qty"]) for o in f.sent]
        assert got == [("C23100", 130), ("C23150", 130)], got
    with_ladder(check)


def test_weekly_and_atm_are_honoured_or_asked_about():
    def check(f):
        r = agent.handle("buy bank nifty weekly call", YES)
        assert not f.sent and "weekly" in r["speak"], r["speak"]
        r = agent.handle("buy nifty 23100 atm call", YES)
        assert not f.sent and "Say one" in r["speak"], r["speak"]
        agent.handle("buy nifty weekly atm call", YES)
        assert len(f.sent) == 1, f.sent
    with_ladder(check)


def test_half_closes_half_in_whole_units():
    held = [pos("NIFTY29SEP26C23100", 195)]            # three lots
    with Fake(positions=lambda include_closed=False: held) as f:
        agent.handle("close half my nifty call", YES)
        assert f.sent and f.sent[0]["qty"] == 65, f.sent  # 1 of 3, down
    with Fake(positions=lambda include_closed=False: [
            pos("NIFTY29SEP26C23100", 65)]) as f:
        r = agent.handle("close half my nifty call", YES)
        assert not f.sent and "can't be halved" in r["speak"], r["speak"]
    shares = [{"symbol": "YESBANK-EQ", "qty": 51, "avg_price": 22.0,
               "ltp": 22.44, "prd": "I"}]
    with Fake(positions=lambda include_closed=False: shares) as f:
        agent.handle("sell half my yes bank", YES)
        assert f.sent and f.sent[0]["qty"] == 25, f.sent


def test_words_with_no_job_and_rupee_amounts_are_asked_about():
    shown = []
    with Fake() as f:
        for said in ("buy nifty call stop loss 5", "buy yes bank worth 500",
                     "is my nifty call closed?", "I won't buy nifty call",
                     "buy two fifty yes bank", "exit all yes bank"):
            r = agent.handle(said, lambda p: shown.append(p) or True)
            assert r.get("blocked") or r.get("cancelled"), (said, r)
        assert not shown and not f.sent

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
