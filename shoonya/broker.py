"""Intent-level operations, shaped for a voice agent to call as tools.

Each function takes plain arguments a speech-to-intent layer can produce and
returns a dict that is easy to read back out loud.

Two rules run through this module:

- An error is never allowed to look like an empty result. The broker answers
  "no positions" and "session expired" with the same status; the SDK turns
  both into None, and "you have no positions" is then a lie. BrokerError
  exists so that cannot happen.
- An order's outcome is never guessed. A dropped connection after sending
  does not mean the order was rejected - it may be live. When the outcome is
  unknown, it is reconciled against the order book or reported as unknown.
"""

import json
import time
import urllib.parse
import uuid

import requests

from shoonya.client import HOST, TIMEOUT, connect

# Order states that will not change any more.
FINAL = {"COMPLETE", "REJECTED", "CANCELED"}

_api = None


class BrokerError(Exception):
    """The broker could not be reached, or refused the request.

    Deliberately distinct from an empty result, so nothing downstream can
    report "you have no positions" when the truth is "I could not ask".
    """


def api():
    global _api
    if _api is None:
        _api = connect(interactive=False)
    return _api


def _ids():
    a = api()
    return (getattr(a, "_NorenApi__username", None),
            getattr(a, "_NorenApi__accountid", None))


def _raw_post(path, values):
    """POST the way the SDK does, but keep the whole response.

    The SDK discards the broker's `emsg` on failure - the only field that
    says why. Network failures come back marked `_network`, so callers can
    tell "the broker said no" from "we never heard back".
    """
    a = api()
    headers = getattr(a, "_NorenApi__OAuthHeaders", None)
    if not headers:
        return {"stat": "Not_Ok",
                "emsg": "No auth headers - session not established"}
    try:
        res = requests.post(f"{HOST}{path}",
                            data="jData=" + json.dumps(values),
                            headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        return {"stat": "Not_Ok", "emsg": f"Network error: {e}",
                "_network": True}
    try:
        return res.json()
    except ValueError:
        return {"stat": "Not_Ok",
                "emsg": f"HTTP {res.status_code}, non-JSON body: "
                        f"{res.text[:300]!r}"}


def _is_empty(res):
    """The broker's way of saying "nothing here" - not an error."""
    return (isinstance(res, dict) and res.get("stat") == "Not_Ok"
            and "no data" in (res.get("emsg") or "").lower())


def _book(path, with_account=True):
    """A list endpoint: rows, [] when genuinely empty, BrokerError otherwise."""
    uid, actid = _ids()
    values = {"uid": uid}
    if with_account:
        values["actid"] = actid
    res = _raw_post(path, values)
    if isinstance(res, list):
        return res
    if _is_empty(res):
        return []
    raise BrokerError(res.get("emsg") or f"Unexpected reply from {path}")


def order_book():
    return _book("/OrderBook", with_account=False)


def positions(include_closed=False):
    """Open positions with both flavours of P&L.

    `rpnl` is REALISED - it stays 0.00 while a position is open, so it is
    the wrong number to read back for "how am I doing". `urmtom` is the
    unrealised mark-to-market, which is what a holder actually wants.

    Raises BrokerError if the book cannot be read. An empty list means the
    account genuinely holds nothing.
    """
    out = []
    for r in _book("/PositionBook"):
        qty = int(r.get("netqty") or 0)
        # A closed position stays in the book all day as a qty=0 row.
        # "What do I own" must not read those back.
        if qty == 0 and not include_closed:
            continue
        avg, ltp = _f(r.get("netavgprc")), _f(r.get("lp"))
        out.append({
            "symbol": r["tsym"],
            "exchange": r.get("exch"),
            "token": r.get("token"),
            "qty": qty,
            "avg_price": avg,
            "ltp": ltp,
            "unrealised_pnl": _f(r.get("urmtom")),
            "realised_pnl": _f(r.get("rpnl")),
            "value": round(qty * ltp, 2) if (ltp and qty) else None,
            # Product code (C/I/M) - an exit has to use the same one.
            "prd": r.get("prd"),
            "product": r.get("s_prdt_ali") or r.get("prd"),
        })
    return out


def funds():
    """Buying power, not just `cash`.

    `cash` is settled cash from prior days and reads 0.00 even when a
    same-day payin has landed. The usable figure for equity is mr_eqt_a.
    """
    uid, actid = _ids()
    lim = _raw_post("/Limits", {"uid": uid, "actid": actid})
    if not isinstance(lim, dict) or lim.get("stat") != "Ok":
        raise BrokerError((lim or {}).get("emsg") or "Could not read funds")
    cash = _f(lim.get("cash")) or 0.0
    payin = _f(lim.get("payin")) or 0.0
    equity_margin = _f(lim.get("mr_eqt_a"))
    return {
        "available": equity_margin if equity_margin is not None else cash + payin,
        "cash_settled": cash,
        "payin_today": payin,
        "equity_margin": equity_margin,
        "blocked": _f(lim.get("blk_amt")),
        "uncleared": _f(lim.get("unclearedcash")),
    }


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- options ---------------------------------------------------------------

def quote_checked(exchange, token, expect_tsym=None, tries=4):
    """get_quotes, but verify the response is for the instrument we asked for.

    The quote endpoint intermittently returns the PREVIOUSLY requested
    instrument instead of the one requested - observed returning the Nifty
    index (23188) in place of an option premium (106). Pricing an order off
    that would size it ~200x too large, so every quote used for an order
    must be identity-checked, not just status-checked.
    """
    for _ in range(tries):
        try:
            q = api().get_quotes(exchange=exchange, token=str(token))
        except (requests.RequestException, ValueError):
            continue            # timed out or garbled - try again
        if not q or q.get("stat") != "Ok":
            continue
        if str(q.get("token", "")) == str(token):
            return q
        if expect_tsym and q.get("tsym") == expect_tsym:
            return q
        # Wrong instrument came back - discard and ask again.
    return None


def option_contract(name, option_type, strike=None, expiry=None):
    """Resolve an index option and attach a live, identity-checked quote.

    name: an index from shoonya.underlyings (NIFTY, BANKNIFTY, SENSEX).
    option_type: 'CE' or 'PE'. Strike defaults to at-the-money, from the
    index's own spot; expiry defaults to the nearest tradeable one.
    """
    from shoonya import instruments as ins
    from shoonya import underlyings

    u = underlyings.get(name)
    spot = None
    if strike is None:
        idx = quote_checked(*u.spot)
        if not idx:
            return {"error": f"Could not read the {u.spoken} level"}
        spot = float(idx["lp"])

    c = ins.find(name, option_type, strike=strike, expiry=expiry, spot=spot)
    if not c:
        return {"error": f"No {u.spoken} {option_type} contract found"}

    q = quote_checked(u.segment, c["token"], expect_tsym=c["tsym"])
    if not q:
        return {"error": f"No trustworthy quote for {c['tsym']} - either the "
                         f"{u.segment} segment is disabled, or the feed kept "
                         f"returning a different instrument.",
                "contract": c}

    today = ins._now_ist().date()
    return {
        "tsym": c["tsym"], "token": c["token"], "exchange": u.segment,
        "underlying": name, "spoken_name": u.spoken, "cadence": u.cadence,
        "lot": c["lot"], "tick": c["tick"], "strike": c["strike"],
        "expiry": str(c["expiry"]), "expires_today": c["expiry"] == today,
        "option_type": option_type,
        "ltp": _f(q.get("lp")), "bid": _f(q.get("bp1")), "ask": _f(q.get("sp1")),
        "spot": _f(q.get("sptprc")) or spot, "oi": q.get("oi"),
        "lower_circuit": _f(q.get("lc")), "upper_circuit": _f(q.get("uc")),
        "strike_adjusted_from": c.get("strike_adjusted_from"),
    }


def quote_view(q):
    """Normalise a raw quote into the fields pricing needs."""
    return {
        "tick": _f(q.get("ti")) or 0.05,
        "bid": _f(q.get("bp1")), "ask": _f(q.get("sp1")),
        "ltp": _f(q.get("lp")),
        "lower_circuit": _f(q.get("lc")), "upper_circuit": _f(q.get("uc")),
    }


def marketable_price(side, quote, buffer_ticks=2):
    """A limit price that crosses the spread, so it fills like a market order.

    Shoonya rejects MKT outright (only LMT and SL-LMT are accepted), so
    "buy at market" means a limit placed through the touch. The buffer
    absorbs a tick or two of movement between quoting and arriving; the
    worst case is still bounded, which a true market order would not be.
    """
    tick = quote.get("tick") or 0.05
    if side == "B":
        base = quote.get("ask") or quote.get("ltp")
        if base is None:
            return None
        price = base + buffer_ticks * tick
        cap = quote.get("upper_circuit")
        if cap:
            price = min(price, cap)
    else:
        base = quote.get("bid") or quote.get("ltp")
        if base is None:
            return None
        price = base - buffer_ticks * tick
        floor = quote.get("lower_circuit")
        if floor:
            price = max(price, floor)
    return round(round(price / tick) * tick, 2)


def place(side, tsym, quantity, price, exchange, product):
    """Send one limit order for an already-resolved symbol.

    The only way an order leaves this program. The symbol is sent exactly
    as given - nothing is looked up again after the user confirmed it.

    Returns a dict whose `status` is one of:
      ACCEPTED - the broker took it; see `order_no`. Not yet a fill.
      REJECTED - the broker explicitly refused; see `reason`.
      UNKNOWN  - the reply never arrived, and the order could not be found
                 in the book. It may or may not be live.
    """
    uid, actid = _ids()
    # A tag unique to this order, so it can be found in the order book if
    # the reply to the placement is lost.
    tag = f"sayso-{uuid.uuid4().hex[:10]}"
    values = {
        "ordersource": "API",
        "uid": uid,
        "actid": actid,
        "trantype": side,
        "prd": product,
        "exch": exchange,
        "tsym": urllib.parse.quote_plus(tsym),
        "qty": str(int(quantity)),
        "dscqty": "0",
        "prctyp": "LMT",
        "prc": str(float(price)),
        "ret": "DAY",
        "remarks": tag,
    }
    out = {"action": "BUY" if side == "B" else "SELL", "symbol": tsym,
           "quantity": int(quantity), "price": price, "exchange": exchange,
           "product": product, "tag": tag}

    res = _raw_post("/PlaceOrder", values)
    out["raw_response"] = res
    if isinstance(res, dict) and res.get("stat") == "Ok" and res.get("norenordno"):
        out["status"] = "ACCEPTED"
        out["order_no"] = res["norenordno"]
        return out

    if not (isinstance(res, dict) and res.get("_network")):
        out["status"] = "REJECTED"
        out["reason"] = ((res or {}).get("emsg")
                         or f"No order number. Response: {res}")
        return out

    # The request went out but no answer came back. The broker may have the
    # order. Reporting REJECTED here invites a retry and a double position,
    # so look for it before saying anything.
    found = _find_by_tag(tag, tsym=tsym, side=side, quantity=quantity)
    if found:
        out["status"] = "ACCEPTED"
        out["order_no"] = found
        out["reconciled"] = True
    else:
        out["status"] = "UNKNOWN"
        out["reason"] = res.get("emsg")
    return out


def _find_by_tag(tag, tsym=None, side=None, quantity=None, tries=3):
    """Find an order we sent, when its placement reply was lost."""
    for attempt in range(tries):
        if attempt:
            time.sleep(1.0)
        try:
            book = order_book()
        except BrokerError:
            continue
        for o in book:
            if o.get("remarks") == tag:
                return o.get("norenordno")
        # Some books omit remarks - fall back to a close match on the
        # newest order, which is at least as strict as a human checking.
        for o in book[:3]:
            if (o.get("tsym") == tsym and o.get("trantype") == side
                    and str(o.get("qty")) == str(int(quantity))):
                return o.get("norenordno")
    return None


def wait_for_outcome(order_no, timeout=10.0, interval=0.75):
    """Follow an accepted order until it fills, is rejected, or time runs out.

    An accepted order is not a fill, and a single look after a fixed delay
    misses fills that land a moment later. Returns the latest known state;
    `final` says whether it can still change.
    """
    deadline = time.monotonic() + timeout
    latest = {"order_no": order_no, "status": "UNKNOWN", "final": False,
              "reason": "Could not read the order book"}
    while True:
        try:
            for o in order_book():
                if o.get("norenordno") == order_no:
                    status = o.get("status")
                    latest = {
                        "order_no": order_no,
                        "status": status,
                        "final": status in FINAL,
                        "symbol": o.get("tsym"),
                        "quantity": _int(o.get("qty")),
                        "filled": _int(o.get("fillshares")),
                        "avg_fill_price": _f(o.get("avgprc")),
                        "price": _f(o.get("prc")),
                        "reason": (o.get("rejreason") or "").strip() or None,
                    }
                    break
            else:
                latest = {"order_no": order_no, "status": "NOT_FOUND",
                          "final": False,
                          "reason": "Accepted, but not yet in the order book"}
        except BrokerError as e:
            latest = {"order_no": order_no, "status": "UNKNOWN",
                      "final": False, "reason": str(e)}
        if latest.get("final") or time.monotonic() >= deadline:
            return latest
        time.sleep(interval)


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0
