"""Placing an order: what gets reported when the reply is lost or garbled.

A gateway error, or a reply without an order number, does not mean the
broker refused - the order may be live. Calling that "rejected" invites a
retry and a doubled position. Runs offline, against a stubbed HTTP layer.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

import shoonya.broker as b  # noqa: E402


class Reply:
    def __init__(self, status, body):
        self.status_code, self.text, self.body = status, str(body), body

    def json(self):
        if isinstance(self.body, str):
            raise ValueError("not JSON")
        return self.body


def placing(reply, book=()):
    """Place one order with the POST answered by `reply`, the book by `book`."""
    saved = (b._api, b.requests.post, b.order_book, b.time.sleep)
    b._api = types.SimpleNamespace(**{
        "_NorenApi__OAuthHeaders": {"Authorization": "x"},
        "_NorenApi__username": "U1", "_NorenApi__accountid": "U1"})

    def post(url, data=None, headers=None, timeout=None):
        if isinstance(reply, Exception):
            raise reply
        return reply
    b.requests.post = post
    b.order_book = lambda: [dict(o, remarks=o.get("remarks")) for o in book]
    b.time.sleep = lambda s: None
    try:
        return b.place("B", "YESBANK-EQ", 1, 22.5, "NSE", "I")
    finally:
        b._api, b.requests.post, b.order_book, b.time.sleep = saved


def test_gateway_error_is_not_a_rejection():
    out = placing(Reply(502, "<html>Bad Gateway</html>"))
    assert out["status"] == "UNKNOWN", out


def test_ok_without_an_order_number_is_not_a_rejection():
    out = placing(Reply(200, {"stat": "Ok"}))
    assert out["status"] == "UNKNOWN", out


def test_an_explicit_refusal_is_a_rejection():
    out = placing(Reply(200, {"stat": "Not_Ok", "emsg": "Insufficient margin"}))
    assert out["status"] == "REJECTED" and "margin" in out["reason"], out


def test_a_lost_reply_finds_the_order_by_its_tag():
    def book_with_ours():
        return [{"norenordno": "NEW", "remarks": placing.tag, "tsym": "YESBANK-EQ",
                 "trantype": "B", "qty": "1"}]
    saved = b.uuid.uuid4
    b.uuid.uuid4 = lambda: types.SimpleNamespace(hex="abcdef012345")
    placing.tag = "sayso-abcdef0123"
    try:
        out = placing(requests.ConnectionError("reset"), book_with_ours())
    finally:
        b.uuid.uuid4 = saved
    assert out["status"] == "ACCEPTED" and out["order_no"] == "NEW", out


def test_a_lost_reply_never_claims_an_older_identical_order():
    # Same symbol, side and size, placed earlier today - not this order.
    older = [{"norenordno": "OLD", "remarks": "sayso-0000000000",
              "tsym": "YESBANK-EQ", "trantype": "B", "qty": "1"},
             {"norenordno": "OLDER", "remarks": None,
              "tsym": "YESBANK-EQ", "trantype": "B", "qty": "1"}]
    out = placing(requests.ConnectionError("reset"), older)
    assert out["status"] == "UNKNOWN", out



def quoting(replies):
    """quote_checked, with get_quotes answering from `replies` in turn."""
    replies = list(replies)
    saved = b._api
    b._api = types.SimpleNamespace(
        get_quotes=lambda exchange, token: replies.pop(0) if replies else None)
    try:
        return b.quote_checked("NFO", "73906", expect_tsym="NIFTY29SEP26C23100")
    finally:
        b._api = saved


NIFTY_INDEX = {"stat": "Ok", "token": "26000", "tsym": "Nifty 50", "lp": "23188"}
THE_OPTION = {"stat": "Ok", "token": "73906", "tsym": "NIFTY29SEP26C23100",
              "lp": "106"}


def test_a_quote_for_another_instrument_is_thrown_away():
    # Observed live: asking for an option returned the Nifty index. Priced
    # off that, an order would be ~200x too large.
    assert quoting([NIFTY_INDEX, THE_OPTION])["lp"] == "106"
    assert quoting([NIFTY_INDEX] * 4) is None


def test_a_garbled_order_book_is_an_error_not_a_crash():
    # null or a string from the order book raised AttributeError - inside
    # the reconciliation of an order that may just have been placed.
    saved = (b._api, b._raw_post)
    b._api = types.SimpleNamespace(**{"_NorenApi__username": "U1",
                                      "_NorenApi__accountid": "U1"})
    try:
        for garbage in (None, "Service Unavailable", ["junk", 3]):
            b._raw_post = lambda path, values, g=garbage: g
            try:
                rows = b.order_book()
            except b.BrokerError:
                continue
            assert rows == [], (garbage, rows)
    finally:
        b._api, b._raw_post = saved
    out = placing(requests.ConnectionError("reset"), book=())
    assert out["status"] == "UNKNOWN", out

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
