"""Intent-level operations, shaped for a voice agent to call as tools.

Each function takes plain arguments a speech-to-intent layer can produce
("buy 5 reliance at market") and returns a dict that is easy to read back
out loud. Orders are dry-run unless `live=True` is passed explicitly.
"""

import json
import time
import urllib.parse

import requests

from shoonya.client import HOST, connect

_api = None


def api():
    global _api
    if _api is None:
        _api = connect(interactive=False)
    return _api


def _raw_post(path, values):
    """POST the way the SDK does, but keep the whole response.

    NorenApi.place_order returns None for any non-Ok status, discarding the
    broker's `emsg` - which is the only thing that says *why* it failed.
    """
    a = api()
    headers = getattr(a, "_NorenApi__OAuthHeaders", None)
    if not headers:
        return {"stat": "Not_Ok", "emsg": "No auth headers - session not established"}
    try:
        res = requests.post(f"{HOST}{path}",
                            data="jData=" + json.dumps(values), headers=headers)
    except requests.RequestException as e:
        return {"stat": "Not_Ok", "emsg": f"Network error: {e}"}
    try:
        return res.json()
    except ValueError:
        return {"stat": "Not_Ok",
                "emsg": f"HTTP {res.status_code}, non-JSON body: {res.text[:300]!r}"}


def resolve_symbol(spoken_name, exchange="NSE"):
    """'reliance' -> {'tsym': 'RELIANCE-EQ', 'token': '2885'}.

    Returns the best match plus alternatives, so the agent can ask
    "did you mean ...?" instead of guessing on an ambiguous name.
    """
    res = api().searchscrip(exchange=exchange, searchtext=spoken_name)
    if not res or not res.get("values"):
        return None
    values = res["values"]
    # Indices come back with nontrd=1 - quotable, never orderable. If one
    # matched, refuse rather than silently substituting a different
    # instrument: a voice user who says "nifty" must not get an ETF by
    # surprise. Make them name it.
    blocked = [v for v in values if v.get("nontrd", "0") == "1"]
    tradeable = [v for v in values if v.get("nontrd", "0") != "1"]
    if blocked:
        return {"error": f"{blocked[0]['tsym']} is an index, not a tradeable "
                         f"instrument.",
                "not_tradeable": [v["tsym"] for v in blocked],
                "did_you_mean": [v["tsym"] for v in tradeable[:5]],
                "needs_confirmation": True}
    if not tradeable:
        return {"error": f"No tradeable instrument matching {spoken_name!r}"}
    values = tradeable
    # Prefer the cash-segment equity line when one exists.
    best = next((v for v in values if v["tsym"].endswith("-EQ")), values[0])
    return {
        "tsym": best["tsym"],
        "token": best["token"],
        "exchange": exchange,
        "alternatives": [v["tsym"] for v in values[:5] if v["tsym"] != best["tsym"]],
    }


def quote(spoken_name, exchange="NSE"):
    sym = resolve_symbol(spoken_name, exchange)
    if not sym:
        return {"error": f"No instrument matching {spoken_name!r} on {exchange}"}
    q = api().get_quotes(exchange=exchange, token=sym["token"])
    if not q:
        return {"error": f"No quote for {sym['tsym']}"}
    return {
        "symbol": q["tsym"],
        "ltp": float(q["lp"]),
        "open": _f(q.get("o")), "high": _f(q.get("h")),
        "low": _f(q.get("l")), "prev_close": _f(q.get("c")),
        "change_pct": _f(q.get("pc")),
    }


def order(side, spoken_name, quantity, price=None, exchange="NSE",
          product="C", live=False):
    """side: 'B' or 'S'. price=None means a market order.

    product: 'C' delivery/CNC, 'I' intraday/MIS, 'M' margin.
    Returns a preview dict unless live=True, so a voice flow can read the
    preview back and place the order only after the user confirms.
    """
    sym = resolve_symbol(spoken_name, exchange)
    if not sym:
        return {"error": f"No instrument matching {spoken_name!r} on {exchange}"}
    if "error" in sym:
        return sym

    q = quote_checked(exchange, sym["token"], expect_tsym=sym["tsym"]) or {}
    low, high = _f(q.get("lc")), _f(q.get("uc"))
    tick = _f(q.get("ti")) or 0.05

    # Shoonya rejects MKT outright, so "at market" is a limit priced
    # through the touch. There is no order type here other than LMT.
    price_type = "LMT"
    if price is None:
        price = marketable_price(side, quote_view(q))
        if price is None:
            return {"error": f"No usable price for {sym['tsym']}"}
    else:
        # The exchange rejects anything outside the daily circuit band, and
        # anything off the tick grid. Catch both before burning an order.
        price = round(round(float(price) / tick) * tick, 2)
        if low and price < low:
            return {"error": f"Price {price} is below {sym['tsym']}'s lower circuit "
                             f"{low} - the exchange will reject it",
                    "circuit": {"lower": low, "upper": high}}
        if high and price > high:
            return {"error": f"Price {price} is above {sym['tsym']}'s upper circuit "
                             f"{high} - the exchange will reject it",
                    "circuit": {"lower": low, "upper": high}}
    preview = {
        "action": "BUY" if side == "B" else "SELL",
        "symbol": sym["tsym"],
        "quantity": int(quantity),
        "price_type": price_type,
        "price": price,
        "product": product,
        "exchange": exchange,
        "estimated_ltp": _f(q.get("lp")),
        "circuit": {"lower": low, "upper": high},
    }
    if not live:
        preview["status"] = "DRY_RUN — pass live=True to actually send this"
        return preview

    a = api()
    values = {
        "ordersource": "API",
        "uid": getattr(a, "_NorenApi__username", None),
        "actid": getattr(a, "_NorenApi__accountid", None),
        "trantype": side,
        "prd": product,
        "exch": exchange,
        "tsym": urllib.parse.quote_plus(sym["tsym"]),
        "qty": str(int(quantity)),
        "dscqty": "0",
        "prctyp": price_type,
        "prc": str(float(price or 0.0)),
        "ret": "DAY",
        "remarks": "voice-agent",
    }
    res = _raw_post("/PlaceOrder", values)

    if res.get("stat") == "Ok" and res.get("norenordno"):
        # `Ok` means the broker RECEIVED it, not that the exchange took it.
        # The real outcome lands in the order book moments later.
        preview["status"] = "ACCEPTED_PENDING"
        preview["order_no"] = res["norenordno"]
    else:
        preview["status"] = "REJECTED"
        preview["reason"] = res.get("emsg") or f"No order number. Full response: {res}"
    preview["raw_response"] = res
    return preview


def order_status(order_no, wait=1.5):
    """The order's real fate. Never trust place_order's response alone.

    Returns the terminal-ish state from the order book: COMPLETE, REJECTED,
    OPEN (resting), CANCELED. `wait` gives the exchange a moment to rule.
    """
    time.sleep(wait)
    for o in (api().get_order_book() or []):
        if o.get("norenordno") == order_no:
            return {
                "order_no": order_no,
                "status": o.get("status"),
                "symbol": o.get("tsym"),
                "quantity": o.get("qty"),
                "filled": o.get("fillshares", "0"),
                "avg_fill_price": o.get("avgprc"),
                "price": o.get("prc"),
                "reason": o.get("rejreason"),
            }
    return {"order_no": order_no, "status": "NOT_FOUND",
            "reason": "Not in the order book - check the session is still valid"}


def place_and_confirm(side, spoken_name, quantity, **kwargs):
    """Place an order and report what actually happened to it."""
    result = order(side, spoken_name, quantity, **kwargs)
    if result.get("status") != "ACCEPTED_PENDING":
        return result
    result["outcome"] = order_status(result["order_no"])
    return result


def positions(include_closed=False):
    """Open positions with both flavours of P&L.

    `rpnl` is REALISED - it stays 0.00 while a position is open, so it is
    the wrong number to read back for "how am I doing". `urmtom` is the
    unrealised mark-to-market, which is what a holder actually wants.
    """
    rows = api().get_positions() or []
    out = []
    for r in rows:
        qty = int(r["netqty"])
        # A closed position stays in the book all day as a qty=0 row.
        # "What do I own" must not read those back.
        if qty == 0 and not include_closed:
            continue
        avg, ltp = _f(r.get("netavgprc")), _f(r.get("lp"))
        out.append({
            "symbol": r["tsym"],
            "qty": qty,
            "avg_price": avg,
            "ltp": ltp,
            "unrealised_pnl": _f(r.get("urmtom")),
            "realised_pnl": _f(r.get("rpnl")),
            "value": round(qty * ltp, 2) if (ltp and qty) else None,
            "product": r.get("s_prdt_ali") or r.get("prd"),
        })
    return out


def funds():
    """Buying power, not just `cash`.

    `cash` is settled cash from prior days and reads 0.00 even when a
    same-day payin has landed. The usable figure for equity is mr_eqt_a.
    """
    lim = api().get_limits() or {}
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
        q = api().get_quotes(exchange=exchange, token=str(token))
        if not q or q.get("stat") != "Ok":
            continue
        if str(q.get("token", "")) == str(token):
            return q
        if expect_tsym and q.get("tsym") == expect_tsym:
            return q
        # Wrong instrument came back - discard and ask again.
    return None


def option_contract(option_type, symbol="NIFTY", strike=None, expiry=None):
    """Resolve the ATM (or given) option and attach a live quote.

    option_type: 'CE' or 'PE'. Strike defaults to at-the-money, derived from
    spot; expiry defaults to the nearest upcoming one.
    """
    from shoonya import instruments as ins

    spot = None
    if strike is None:
        # Spot from the index itself; NSE works even when NFO search does not.
        idx = quote_checked("NSE", "26000")
        if not idx:
            return {"error": "Could not read Nifty spot"}
        spot = float(idx["lp"])

    c = ins.find(symbol=symbol, option_type=option_type,
                 strike=strike, expiry=expiry, spot=spot)
    if not c:
        return {"error": f"No {symbol} {option_type} contract found"}

    q = quote_checked("NFO", c["token"], expect_tsym=c["tsym"])
    if not q:
        return {"error": f"No trustworthy quote for {c['tsym']} - either the "
                         f"F&O segment is disabled, or the feed kept returning "
                         f"a different instrument.",
                "contract": c}

    return {
        "tsym": c["tsym"], "token": c["token"], "exchange": "NFO",
        "lot": c["lot"], "tick": c["tick"], "strike": c["strike"],
        "expiry": str(c["expiry"]), "option_type": option_type,
        "ltp": _f(q.get("lp")), "bid": _f(q.get("bp1")), "ask": _f(q.get("sp1")),
        "spot": _f(q.get("sptprc")), "oi": q.get("oi"),
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


def place_direct(side, tsym, quantity, price, exchange="NFO", product="M",
                 remarks="sayso"):
    """Place an order for an already-resolved symbol.

    The equity path searches for the instrument by spoken name; derivatives
    come pre-resolved from the contract master, so this skips resolution
    and sends exactly the symbol given.
    """
    a = api()
    values = {
        "ordersource": "API",
        "uid": getattr(a, "_NorenApi__username", None),
        "actid": getattr(a, "_NorenApi__accountid", None),
        "trantype": side,
        "prd": product,
        "exch": exchange,
        "tsym": urllib.parse.quote_plus(tsym),
        "qty": str(int(quantity)),
        "dscqty": "0",
        "prctyp": "LMT",
        "prc": str(float(price)),
        "ret": "DAY",
        "remarks": remarks,
    }
    res = _raw_post("/PlaceOrder", values)
    out = {"action": "BUY" if side == "B" else "SELL", "symbol": tsym,
           "quantity": int(quantity), "price": price, "exchange": exchange,
           "product": product, "raw_response": res}
    if res.get("stat") == "Ok" and res.get("norenordno"):
        out["status"] = "ACCEPTED_PENDING"
        out["order_no"] = res["norenordno"]
    else:
        out["status"] = "REJECTED"
        out["reason"] = res.get("emsg") or f"No order number. Response: {res}"
    return out


def place_direct_and_confirm(side, tsym, quantity, price, **kwargs):
    result = place_direct(side, tsym, quantity, price, **kwargs)
    if result.get("status") != "ACCEPTED_PENDING":
        return result
    result["outcome"] = order_status(result["order_no"])
    return result
