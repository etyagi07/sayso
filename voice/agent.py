"""Intent -> resolved order -> typed confirmation -> execution.

The confirmation step is the point of the whole design. Everything upstream
(ASR, parser) can be wrong, so nothing goes live until a human has read the
resolved instrument, the price, and the total cost on screen and pressed y.
"""

import shoonya.broker as b
from voice import safety, strikes
from voice.parser import parse


def handle(transcript, confirm=None):
    """Run one utterance. `confirm` takes a preview dict, returns bool."""
    try:
        return _handle(transcript, confirm)
    except b.BrokerError as e:
        # Only reads raise this - placing an order never does - so nothing
        # has been sent. Say so, rather than letting a failed read pass as
        # "you have no positions".
        return {"speak": f"I couldn't reach the broker, so I haven't done "
                         f"anything. {e}", "blocked": True, "broker_error": True}


def _handle(transcript, confirm):
    intent = parse(transcript)
    kind = intent["intent"]

    if kind == "unknown":
        return {"speak": "Sorry, I didn't catch an instruction in that.",
                "intent": intent}
    if kind == "funds":
        f = b.funds()
        return {"speak": f"You have {f['available']:.2f} rupees available.",
                "data": f}
    if kind == "positions":
        pos = b.positions()
        if not pos:
            return {"speak": "You have no open positions.", "data": []}
        parts = [f"{p['qty']} {p['symbol'].replace('-EQ','')} at {p['avg_price']:.2f}, "
                 f"now {p['ltp']:.2f}" for p in pos]
        return {"speak": "You hold " + "; ".join(parts), "data": pos}
    if kind == "orders":
        book = b.order_book()
        live = [o for o in book if o.get("status") in ("OPEN", "TRIGGER_PENDING")]
        return {"speak": f"{len(live)} open of {len(book)} orders today.",
                "data": live}
    if kind == "limits":
        s = safety.status()
        lot_words = ("no lot limit" if s['max_lots'] is None
                     else f"{s['max_lots']} lot maximum")
        return {"speak": (f"Options: {', '.join(s['option_allowlist'])} only, "
                          f"{lot_words}, premium up to "
                          f"{s['max_premium_per_unit']:.0f}. "
                          f"Equity: {', '.join(s['allowlist'])} only, "
                          f"{s['max_order_value']:.0f} rupees per order. "
                          f"{s['orders_remaining']} orders left today."),
                "data": s}
    if kind == "quote":
        q = b.quote(intent["name"])
        if "error" in q:
            return {"speak": q["error"], "data": q}
        # change_pct is absent for some instruments - compute from prev close.
        pct = q.get("change_pct")
        if pct is None and q.get("prev_close"):
            pct = (q["ltp"] - q["prev_close"]) / q["prev_close"] * 100
        move = f", {pct:+.2f} percent" if pct is not None else ""
        return {"speak": f"{q['symbol'].replace('-EQ','')} is at "
                         f"{q['ltp']:.2f}{move}.", "data": q}

    if kind in ("option_buy", "option_exit", "option_quote", "option_refused"):
        return _handle_option(intent, confirm)

    if kind != "order":
        return {"speak": "I'm not sure what to do with that.", "intent": intent}

    # --- order path ------------------------------------------------------
    sym = b.resolve_symbol(intent["name"])
    if not sym:
        return {"speak": f"I couldn't find anything called {intent['name']}."}
    if "error" in sym:
        suggestions = sym.get("did_you_mean") or []
        extra = f" Did you mean {suggestions[0]}?" if suggestions else ""
        return {"speak": sym["error"] + extra, "data": sym}
    if sym.get("alternatives"):
        # Never guess between instruments - this is the IDEAFORGE trap.
        return {"speak": f"{intent['name']} is ambiguous. It could be "
                         f"{sym['tsym']} or {', '.join(sym['alternatives'][:2])}. "
                         f"Please say the exact name.",
                "data": sym, "needs_disambiguation": True}

    q = b.quote_checked("NSE", sym["token"], expect_tsym=sym["tsym"])
    if not q:
        return {"speak": f"I couldn't get a reliable price for "
                         f"{sym['tsym']}.", "blocked": True}
    ltp = b._f(q.get("lp"))
    side_word = "buy" if intent["side"] == "B" else "sell"
    opening = intent["side"] == "B"
    product = "C"

    quantity = intent["quantity"]
    if not opening:
        # A sell closes something you hold. Selling more than that would
        # be opening a short - refused, as selling options to open is.
        position = next((p for p in b.positions()
                         if p["symbol"] == sym["tsym"] and p["qty"] > 0), None)
        if position is None:
            return {"speak": f"You don't hold any {sym['tsym']} to sell."}
        held = position["qty"]
        if quantity is None:
            quantity = held
        elif quantity > held:
            return {"speak": f"You hold {held} {sym['tsym']}. I won't sell "
                             f"more than you hold.", "blocked": True}
        product = position.get("prd") or product
    elif quantity is None:
        return {"speak": f"How many {sym['tsym']} do you want to {side_word}?",
                "needs_quantity": True, "data": sym}

    # Everything goes at market: a limit priced through the touch so it
    # fills now. A price said out loud is shown on the confirmation screen
    # rather than executed - numbers are the least reliable thing in speech.
    spoken_price = intent["price"]
    price = b.marketable_price(intent["side"], b.quote_view(q))
    if price is None:
        return {"speak": f"No usable price for {sym['tsym']}.", "blocked": True}

    value = round(quantity * price, 2)
    if opening:
        # Limits apply to opening a position. Closing one is never blocked -
        # a cap that stops you exiting traps you in the trade.
        try:
            value = safety.check(sym["tsym"], quantity, price, "LMT")
        except safety.Rejected as e:
            return {"speak": str(e), "blocked": True}

    preview = {
        "action": side_word.upper(), "symbol": sym["tsym"], "quantity": quantity,
        "price": price, "value": value, "ltp": ltp, "at_market": True,
        "spoken_price": spoken_price,
        "bid": b._f(q.get("bp1")), "ask": b._f(q.get("sp1")),
        "tick": b._f(q.get("ti")) or 0.05,
        "lower_circuit": b._f(q.get("lc")), "upper_circuit": b._f(q.get("uc")),
        "spoken": (f"{side_word} {quantity} {sym['tsym'].replace('-EQ','')} "
                   f"at market, about {price:.2f}, "
                   f"total {value:.2f} rupees."),
    }

    if confirm is None or not confirm(preview):
        return {"speak": "Cancelled.", "preview": preview, "confirmed": False}

    # The confirmation screen can change the price; send what was shown.
    price = preview["price"]
    value = round(quantity * price, 2)
    if opening:
        try:
            value = safety.check(sym["tsym"], quantity, price, "LMT")
        except safety.Rejected as e:
            return {"speak": str(e), "blocked": True}

    name = sym["tsym"].replace("-EQ", "")
    return _execute(intent["side"], sym["tsym"], quantity, price,
                    exchange="NSE", product=product,
                    did=f"{'bought' if opening else 'sold'} {quantity} {name}",
                    value=value, opening=opening, segment="equity")


# --- options ---------------------------------------------------------------

_SPOKEN = {"CE": "call", "PE": "put"}


def _ladder_and_spot():
    """The strikes actually listed for the nearest expiry, plus spot."""
    from shoonya import instruments as ins
    idx = b.quote_checked("NSE", "26000")
    if not idx:
        return None, None
    spot = float(idx["lp"])
    expiries = ins.expiries()
    if not expiries:
        return None, None
    ladder = {x["strike"] for x in ins.load(symbol="NIFTY")
              if x["expiry"] == expiries[0]}
    return ladder, spot


def _expiry_words(iso):
    """'2026-09-29' -> '29 Sep' - short enough to say, precise enough to act on."""
    from datetime import date
    try:
        d = date.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)
    return f"{d.day} {d.strftime('%b')}"


def _handle_option(intent, confirm):
    kind = intent["intent"]
    opt = intent["option_type"]
    word = _SPOKEN[opt]

    if kind == "option_refused":
        return {"speak": intent["reason"], "blocked": True}

    if kind == "option_exit":
        return _exit_option(opt, confirm)

    # Quantity and strike both come from the numbers that were spoken;
    # telling them apart needs the live ladder, so it happens here.
    lots = strike = None
    if kind == "option_buy":
        ladder, spot = _ladder_and_spot()
        if ladder is None:
            return {"speak": "Could not read the Nifty level.", "blocked": True}
        lots, strike, err = strikes.read_order(
            intent.get("number_spans"), ladder, spot)
        if err:
            return {"speak": err, "blocked": True, "needs_clarification": True}

    c = b.option_contract(opt, strike=strike)
    if "error" in c:
        return {"speak": c["error"], "blocked": True}

    if kind == "option_quote":
        return {"speak": f"The {_expiry_words(c['expiry'])} {c['strike']} {word} "
                         f"is at {c['ltp']:.2f}, Nifty at {c['spot']:.0f}.",
                "data": c}

    # --- buy to open ---------------------------------------------------
    lots = int(lots or 1)
    price = b.marketable_price("B", c)
    if price is None:
        return {"speak": f"No usable price for {c['tsym']}.", "blocked": True}

    try:
        value, units = safety.check_option(c["tsym"], "NIFTY", lots,
                                           c["lot"], price)
    except safety.Rejected as e:
        return {"speak": str(e), "blocked": True}

    adjusted = ""
    if c.get("strike_adjusted_from"):
        adjusted = (f" Nearest listed strike to "
                    f"{c['strike_adjusted_from']} was used.")

    preview = {
        "action": "BUY", "symbol": c["tsym"], "quantity": units,
        "lots": lots, "price": price, "value": value, "ltp": c["ltp"],
        "at_market": True, "spoken_price": None,
        "bid": c["bid"], "ask": c["ask"], "tick": c["tick"],
        "lower_circuit": c["lower_circuit"], "upper_circuit": c["upper_circuit"],
        "expiry": c["expiry"], "strike": c["strike"],
        "spoken": (f"buy {lots} lot{'s' if lots != 1 else ''} of the "
                   f"{_expiry_words(c['expiry'])} "
                   f"{c['strike']} {word}, {units} units at market, "
                   f"about {price:.2f}, total {value:,.0f} rupees. "
                   f"Nifty at {c['spot']:.0f}." + adjusted),
    }
    if confirm is None or not confirm(preview):
        return {"speak": "Cancelled.", "preview": preview, "confirmed": False}

    price = preview["price"]
    try:
        value, units = safety.check_option(c["tsym"], "NIFTY", lots,
                                           c["lot"], price)
    except safety.Rejected as e:
        return {"speak": str(e), "blocked": True}

    return _execute("B", c["tsym"], units, price, exchange="NFO",
                    product="M",
                    did=(f"bought {lots} lot{'s' if lots != 1 else ''} of "
                         f"the {c['strike']} {word}"),
                    value=value, opening=True, segment="options")


def _exit_option(opt, confirm):
    word = _SPOKEN[opt]
    held = [p for p in b.positions()
            if p["symbol"].startswith("NIFTY")
            and _opt_type_of(p["symbol"]) == opt and p["qty"] != 0]
    if not held:
        return {"speak": f"You have no open {word} position."}
    if len(held) > 1:
        # Two positions answer to "exit call". Closing either one on a
        # guess is exactly what this program refuses to do elsewhere.
        names = " and ".join(p["symbol"] for p in held)
        return {"speak": f"You have {len(held)} open {word}s: {names}. "
                         f"Say which strike to exit.",
                "blocked": True, "needs_clarification": True,
                "data": held}

    pos = held[0]
    qty = abs(pos["qty"])
    q = b.quote_checked("NFO", _token_for(pos["symbol"]), pos["symbol"])
    if not q:
        return {"speak": f"Could not price {pos['symbol']} to exit it.",
                "blocked": True}

    ltp = b._f(q.get("lp"))
    # What the position is actually worth right now, in rupees - a trader
    # exiting wants the number, not two prices to subtract in their head.
    pnl = None
    if ltp is not None and pos.get("avg_price"):
        per_unit = ltp - pos["avg_price"]
        pnl = round(per_unit * pos["qty"], 2)

    side = "S" if pos["qty"] > 0 else "B"
    price = b.marketable_price(side, {
        "tick": b._f(q.get("ti")) or 0.05,
        "bid": b._f(q.get("bp1")), "ask": b._f(q.get("sp1")),
        "ltp": b._f(q.get("lp")),
        "lower_circuit": b._f(q.get("lc")), "upper_circuit": b._f(q.get("uc")),
    })
    value = round(qty * price, 2)
    if pnl is None:
        result_words = ""
    elif pnl >= 0:
        result_words = f" You're up {pnl:,.0f} rupees."
    else:
        result_words = f" You're down {abs(pnl):,.0f} rupees."

    preview = {
        "action": "EXIT " + ("SELL" if side == "S" else "BUY"),
        "symbol": pos["symbol"], "quantity": qty, "lots": None,
        "price": price, "value": value, "ltp": ltp, "at_market": True,
        "pnl": pnl, "entry": pos.get("avg_price"),
        "bid": b._f(q.get("bp1")), "ask": b._f(q.get("sp1")),
        "tick": b._f(q.get("ti")) or 0.05,
        "lower_circuit": b._f(q.get("lc")), "upper_circuit": b._f(q.get("uc")),
        "spoken": (f"exit your {word} position, {qty} units at market, "
                   f"about {value:,.0f} rupees. "
                   f"Entry was {pos['avg_price']:.2f}, now {ltp:.2f}."
                   + result_words),
    }
    if confirm is None or not confirm(preview):
        return {"speak": "Cancelled.", "preview": preview, "confirmed": False}

    # The confirmation screen can change the price; send what was shown.
    price = preview["price"]
    value = round(qty * price, 2)
    return _execute(side, pos["symbol"], qty, price, exchange="NFO",
                    product=pos.get("prd") or "M",
                    did=f"exited the {word}", value=value,
                    opening=False, segment="options")


def _opt_type_of(tsym):
    """NIFTY29SEP26C23200 -> 'CE'. The C/P sits before the strike."""
    import re
    m = re.search(r"(\d{2}[A-Z]{3}\d{2})([CP])(\d+)$", tsym)
    if not m:
        return None
    return "CE" if m.group(2) == "C" else "PE"


def _token_for(tsym):
    from shoonya import instruments as ins
    for c in ins.load(symbol="NIFTY"):
        if c["tsym"] == tsym:
            return c["token"]
    return None


def _execute(side, tsym, quantity, price, exchange, product, did, value,
             opening, segment):
    """Send one confirmed order and say, truthfully, what happened to it.

    `outcome` in the reply is one of filled / partial / resting / rejected /
    unknown - what the audio layer keys its sounds off.
    """
    sent = b.place(side, tsym, quantity, price, exchange, product)

    if sent["status"] == "REJECTED":
        return {"speak": f"Rejected by the broker. {sent['reason']}".strip(),
                "outcome": "rejected", "data": sent}

    if sent["status"] == "UNKNOWN":
        # It may be live. Count it, so a retry cannot slip past the caps,
        # and tell the user to look before they try again.
        if opening:
            safety.record(value, segment)
        return {"speak": "I can't confirm that order went through. The "
                         "connection dropped and I couldn't find it in your "
                         "order book. Check your order book before you try "
                         "again.",
                "outcome": "unknown", "data": sent}

    state = b.wait_for_outcome(sent["order_no"])
    status = state.get("status")

    if status in ("REJECTED", "CANCELED"):
        reason = state.get("reason") or status.lower()
        return {"speak": f"Rejected by the exchange. {reason}",
                "outcome": "rejected", "data": {**sent, "state": state}}

    # It reached the market. Only opening trades count against the caps.
    if opening:
        safety.record(value, segment)

    filled, total = state.get("filled") or 0, state.get("quantity") or quantity
    avg = state.get("avg_fill_price")
    if status == "COMPLETE":
        return {"speak": f"Filled. {did} at {avg}.", "outcome": "filled",
                "confirmed": True, "data": {**sent, "state": state}}
    if filled and filled < total:
        return {"speak": f"Part filled: {filled} of {total} at {avg}. The "
                         f"rest is still working.",
                "outcome": "partial", "confirmed": True,
                "data": {**sent, "state": state}}
    if status in ("OPEN", "PENDING", "TRIGGER_PENDING"):
        return {"speak": f"Placed but not filled yet. Order "
                         f"{sent['order_no']} is resting at {price:.2f}.",
                "outcome": "resting", "confirmed": True,
                "data": {**sent, "state": state}}
    return {"speak": f"The broker accepted order {sent['order_no']}, but I "
                     f"couldn't see what happened to it. Check your order "
                     f"book.",
            "outcome": "unknown", "confirmed": True,
            "data": {**sent, "state": state}}
