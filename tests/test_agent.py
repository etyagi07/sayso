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
}


def fake_contract_for(tsym):
    if tsym not in CONTRACTS:
        return None
    u, ot, k, exch, tok = CONTRACTS[tsym]
    return {"tsym": tsym, "underlying": u, "option_type": ot, "strike": k,
            "exchange": exch, "token": tok, "lot": 65,
            "expiry": date(2026, 9, 29)}


def pos(tsym, qty, avg=100.0):
    return {"symbol": tsym, "qty": qty, "avg_price": avg, "ltp": avg + 1,
            "prd": "M"}

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
            "resolve_symbol": lambda name, exchange="NSE": {
                "tsym": "YESBANK-EQ", "token": "11915", "exchange": "NSE",
                "alternatives": []},
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
        r = agent.handle("buy one yesbank", YES)
        assert r["outcome"] == "rejected"
        assert "pending" not in r["speak"].lower()
        assert "Insufficient margin" in r["speak"]
        assert orders_counted() == 0


def test_lost_reply_is_not_called_a_rejection():
    # A dropped connection does not mean the broker refused. Saying
    # "rejected" invites a retry and a doubled position.
    with Fake(place=lambda *a: {"status": "UNKNOWN", "reason": "timeout"}):
        r = agent.handle("buy one yesbank", YES)
        assert r["outcome"] == "unknown"
        assert "rejected" not in r["speak"].lower()
        assert "order book" in r["speak"].lower()
        assert orders_counted() == 1, "possibly live - must count"


def test_exchange_rejection_after_acceptance_is_not_counted():
    with Fake(wait_for_outcome=lambda n, **k: {
            "status": "REJECTED", "final": True, "reason": "Circuit limit"}):
        r = agent.handle("buy one yesbank", YES)
        assert r["outcome"] == "rejected"
        assert "Circuit limit" in r["speak"]
        assert orders_counted() == 0


def test_resting_order_says_resting():
    with Fake(wait_for_outcome=lambda n, **k: {
            "status": "OPEN", "final": False, "quantity": 1, "filled": 0}):
        r = agent.handle("buy one yesbank", YES)
        assert r["outcome"] == "resting"
        assert "not filled" in r["speak"]


def test_partial_fill_says_partial():
    with Fake(wait_for_outcome=lambda n, **k: {
            "status": "OPEN", "final": False, "quantity": 4, "filled": 1,
            "avg_fill_price": 22.45}):
        r = agent.handle("buy four yesbank", YES)
        assert r["outcome"] == "partial"
        assert "1 of 4" in r["speak"]


def test_expired_session_is_not_an_empty_account():
    # Regression: the broker answers "no data" and "session expired" with
    # the same status, and both used to read as "you have no positions".
    def expired(include_closed=False):
        raise b.BrokerError("Session Expired :  Invalid Session Key")
    with Fake(positions=expired):
        for said in ("what do i own", "exit call", "sell my yesbank"):
            r = agent.handle(said, YES)
            assert r.get("broker_error"), said
            assert "no open" not in r["speak"].lower(), said
            assert "don't hold" not in r["speak"].lower(), said


# --- sending what was confirmed ------------------------------------------

def test_confirmed_symbol_is_the_one_sent():
    # Regression: after confirmation the symbol was searched for again.
    looked_up = []
    with Fake(resolve_symbol=lambda name, exchange="NSE": (
            looked_up.append(name) or {
                "tsym": "YESBANK-EQ", "token": "11915",
                "exchange": "NSE", "alternatives": []})) as f:
        agent.handle("buy one yesbank", YES)
        assert looked_up == ["yesbank"], f"searched again: {looked_up}"
        assert f.sent[0]["tsym"] == "YESBANK-EQ"


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


def test_unchecked_quote_never_prices_an_order():
    # Regression: the equity confirm box priced off a raw quote, which the
    # broker sometimes returns for the wrong instrument.
    with Fake(quote_checked=lambda *a, **k: None) as f:
        r = agent.handle("buy one yesbank", YES)
        assert r.get("blocked")
        assert not f.sent


# --- exits are never trapped ---------------------------------------------

def test_equity_exit_is_not_blocked_by_the_size_cap():
    # Regression: a sell to close went through the 100-rupee cap, so a
    # position could be impossible to exit by voice.
    held = [{"symbol": "YESBANK-EQ", "qty": 50, "avg_price": 22.0,
             "ltp": 22.44, "prd": "C"}]
    with Fake(positions=lambda include_closed=False: held) as f:
        r = agent.handle("sell my yesbank", YES)
        assert not r.get("blocked"), r["speak"]
        assert f.sent[0]["qty"] == 50
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
        r = agent.handle("buy one yesbank", NO)
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
