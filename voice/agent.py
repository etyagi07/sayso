"""Intent -> resolved order -> typed confirmation -> execution.

The confirmation step is the point of the whole design. Everything upstream
(ASR, parser) can be wrong, so nothing goes live until a human has read the
resolved instrument, the price, and the total cost on screen and pressed y.
"""

import time

import shoonya.broker as b
from shoonya import instruments as ins
from shoonya import underlyings
from voice import safety, strikes
from voice.parser import parse

# A question the agent asked ("which index?"), and the order waiting on the
# answer. Anything other than an answer drops it, and it expires on its own,
# so a half-finished order can never be completed by accident later.
PENDING_SECONDS = 30
_pending = None


def _set_pending(intent, missing):
    global _pending
    _pending = {"intent": dict(intent), "missing": missing,
                "expires": time.monotonic() + PENDING_SECONDS}


def _take_pending():
    global _pending
    pending, _pending = _pending, None
    if pending and time.monotonic() < pending["expires"]:
        return pending
    return None


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

    pending = _take_pending()
    if pending and intent["intent"] == "index_answer" \
            and pending["missing"] == "underlying":
        intent = {**pending["intent"], "underlying": intent["underlying"]}
    elif pending and intent["intent"] == "number_answer" \
            and pending["missing"] == "strike":
        intent = {**pending["intent"], "number_spans": intent["number_spans"]}
    elif intent["intent"] in ("index_answer", "number_answer"):
        return {"speak": "I wasn't waiting for an answer. Say the whole "
                         "command.", "blocked": True}

    kind = intent["intent"]

    if kind == "index_unsupported":
        return {"speak": f"{intent['index']} options aren't supported. You "
                         f"can trade Nifty, Bank Nifty and Sensex.",
                "blocked": True}
    if kind == "index_needs_type":
        spoken = underlyings.get(intent["underlying"]).spoken
        return {"speak": f"{spoken} is an index - say call or put, for "
                         f"example 'buy {spoken} call'.", "blocked": True}

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
        parts = [f"{p['qty']} {friendly(p['symbol'])} at "
                 f"{(p['avg_price'] or 0):.2f}, now {(p['ltp'] or 0):.2f}"
                 for p in pos]
        return {"speak": "You hold " + "; ".join(parts), "data": pos}
    if kind == "orders":
        book = b.order_book()
        live = [o for o in book if o.get("status") in ("OPEN", "TRIGGER_PENDING")]
        return {"speak": f"{len(live)} open of {len(book)} orders today.",
                "data": live}
    if kind == "limits":
        s = safety.status()
        caps = ", ".join(f"{underlyings.get(n).spoken} {c} lots"
                         for n, c in s["max_lots"].items())
        return {"speak": (f"Options: {caps} per order. "
                          f"{s['option_orders_remaining']} option orders "
                          f"left today. Equity: "
                          f"{', '.join(s['allowlist'])}, "
                          f"{s['max_order_value']:,.0f} rupees per order."),
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


def _ask_index(intent, what):
    """No index was named. Ask, and hold the order until the answer."""
    _set_pending(intent, "underlying")
    return {"speak": f"Which index {what} - Nifty, Bank Nifty or Sensex?",
            "blocked": True, "needs_answer": "underlying"}


def _ladder_and_spot(name):
    """Strikes listed for the nearest tradeable expiry, spot, and that expiry."""
    u = underlyings.get(name)
    idx = b.quote_checked(*u.spot)
    if not idx:
        return None, None, None
    upcoming = ins.expiries(name)
    if not upcoming:
        return None, None, None
    return ins.ladder(name, upcoming[0]), float(idx["lp"]), upcoming[0]


def _other_index_hint(spans, name):
    """If the strike belongs to a different index, say which - never switch."""
    for other in underlyings.UNDERLYINGS:
        if other == name:
            continue
        ladder, spot, _ = _ladder_and_spot(other)
        if not ladder:
            continue
        for span in spans or []:
            found, _ = strikes.resolve(span, ladder, spot,
                                       underlyings.band(other))
            if found:
                spoken = underlyings.get(other).spoken
                return f" {found:,} is a {spoken} strike - did you mean {spoken}?"
    return ""


def _read_numbers(intent, name):
    """Quantity and strike from the spoken numbers, on this index's ladder."""
    ladder, spot, expiry = _ladder_and_spot(name)
    if ladder is None:
        return None, None, None, {"speak": f"Could not read the "
                                           f"{underlyings.get(name).spoken} "
                                           f"level.", "blocked": True}
    lots, strike, err = strikes.read_order(intent.get("number_spans"),
                                           ladder, spot,
                                           band=underlyings.band(name))
    if err:
        hint = _other_index_hint(intent.get("number_spans"), name)
        return None, None, None, {"speak": err + hint, "blocked": True,
                                  "needs_clarification": True}
    return lots, strike, expiry, None


def _expiry_words(iso):
    """'2026-09-29' -> '29 Sep' - short enough to say, precise enough to act on."""
    from datetime import date
    try:
        d = date.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)
    return f"{d.day} {d.strftime('%b')}"


def _when(c):
    """What kind of expiry this is, as it should be read out."""
    if c.get("expires_today"):
        return "expires today"
    return c.get("cadence") or "weekly"


def friendly(tsym):
    """A contract as a person would say it: 'Sensex 1 Oct 73900 call'."""
    c = ins.contract_for(tsym)
    if not c:
        return tsym.replace("-EQ", "")
    return (f"{underlyings.get(c['underlying']).spoken} "
            f"{_expiry_words(c['expiry'])} {c['strike']} "
            f"{_SPOKEN.get(c['option_type'], c['option_type'])}")


def _handle_option(intent, confirm):
    kind = intent["intent"]
    opt = intent["option_type"]
    word = _SPOKEN[opt]
    name = intent.get("underlying")

    if kind == "option_refused":
        return {"speak": intent["reason"], "blocked": True}
    if kind == "option_exit":
        return _exit_option(intent, confirm)
    if name is None:
        return _ask_index(intent, "for that " + word)

    u = underlyings.get(name)
    lots, strike, expiry, problem = _read_numbers(intent, name)
    if problem:
        return problem

    c = b.option_contract(name, opt, strike=strike, expiry=expiry)
    if "error" in c:
        return {"speak": c["error"], "blocked": True}

    if kind == "option_quote":
        return {"speak": f"The {u.spoken} {_expiry_words(c['expiry'])} "
                         f"{c['strike']} {word}, {_when(c)}, is at "
                         f"{c['ltp']:.2f}. {u.spoken} at {c['spot']:,.0f}.",
                "data": c}

    # --- buy to open ---------------------------------------------------
    lots = int(lots or 1)
    price = b.marketable_price("B", c)
    if price is None:
        return {"speak": f"No usable price for {c['tsym']}.", "blocked": True}

    try:
        value, units = safety.check_option(c["tsym"], name, lots,
                                           c["lot"], price)
    except safety.Rejected as e:
        return {"speak": str(e), "blocked": True}

    adjusted = ""
    if c.get("strike_adjusted_from"):
        adjusted = (f" {c['strike_adjusted_from']} isn't listed, so the "
                    f"nearest strike is used.")

    plural = "s" if lots != 1 else ""
    preview = {
        "action": "BUY", "symbol": c["tsym"], "quantity": units,
        "lots": lots, "price": price, "value": value, "ltp": c["ltp"],
        "at_market": True, "spoken_price": None,
        "bid": c["bid"], "ask": c["ask"], "tick": c["tick"],
        "lower_circuit": c["lower_circuit"], "upper_circuit": c["upper_circuit"],
        "expiry": c["expiry"], "strike": c["strike"],
        "underlying": u.spoken, "when": _when(c),
        "spoken": (f"buy {lots} lot{plural} of the {u.spoken} "
                   f"{_expiry_words(c['expiry'])} {c['strike']} {word}, "
                   f"{_when(c)}, {units} units at market, about "
                   f"{price:.2f}, total {value:,.0f} rupees."
                   + adjusted),
    }
    if confirm is None or not confirm(preview):
        return {"speak": "Cancelled.", "preview": preview, "confirmed": False}

    # The confirmation screen can change the price; send what was shown.
    price = preview["price"]
    try:
        value, units = safety.check_option(c["tsym"], name, lots,
                                           c["lot"], price)
    except safety.Rejected as e:
        return {"speak": str(e), "blocked": True}

    return _execute("B", c["tsym"], units, price, exchange=c["exchange"],
                    product="M",
                    did=(f"bought {lots} lot{plural} of the {u.spoken} "
                         f"{c['strike']} {word}"),
                    value=value, opening=True, segment="options")


def _exit_option(intent, confirm):
    opt = intent["option_type"]
    word = _SPOKEN[opt]
    name = intent.get("underlying")

    # What is actually held, looked up in the masters - which recognises
    # every exchange's symbol layout, not just NIFTY's.
    held = []
    for p in b.positions():
        c = ins.contract_for(p["symbol"])
        if not c or c["option_type"] != opt or p["qty"] == 0:
            continue
        if name and c["underlying"] != name:
            continue
        held.append((p, c))

    where = f"{underlyings.get(name).spoken} " if name else ""
    if not held:
        return {"speak": f"You have no open {where}{word} position."}

    spans = intent.get("number_spans") or []
    if spans:
        # A strike was named: keep the positions it can mean.
        wanted = set()
        for span in spans:
            wanted |= strikes.candidates(span)
        held = [(p, c) for p, c in held if c["strike"] in wanted]
        if not held:
            return {"speak": f"You don't hold that {where}{word} strike.",
                    "blocked": True}

    if len(held) > 1:
        # More than one position answers. Closing one on a guess is what
        # this program refuses to do everywhere else.
        indices = {c["underlying"] for _, c in held}
        listed = " and ".join(friendly(p["symbol"]) for p, _ in held)
        if len(indices) > 1:
            _set_pending(intent, "underlying")
            return {"speak": f"You hold {listed}. Which index?",
                    "blocked": True, "needs_answer": "underlying"}
        _set_pending(intent, "strike")
        return {"speak": f"You hold {listed}. Which strike?",
                "blocked": True, "needs_answer": "strike"}

    pos, c = held[0]
    qty = abs(pos["qty"])
    q = b.quote_checked(c["exchange"], c["token"], expect_tsym=pos["symbol"])
    if not q:
        return {"speak": f"Could not price {friendly(pos['symbol'])} to exit "
                         f"it.", "blocked": True}

    ltp = b._f(q.get("lp"))
    # What the position is worth right now, in rupees - a trader exiting
    # wants the number, not two prices to subtract in their head.
    pnl = None
    if ltp is not None and pos.get("avg_price"):
        pnl = round((ltp - pos["avg_price"]) * pos["qty"], 2)

    side = "S" if pos["qty"] > 0 else "B"
    view = b.quote_view(q)
    price = b.marketable_price(side, view)
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
        "bid": view["bid"], "ask": view["ask"], "tick": view["tick"],
        "lower_circuit": view["lower_circuit"],
        "upper_circuit": view["upper_circuit"],
        "spoken": (f"exit your {friendly(pos['symbol'])}, {qty} units at "
                   f"market, about {value:,.0f} rupees. Entry was "
                   f"{pos['avg_price']:.2f}, now {ltp:.2f}." + result_words),
    }
    if confirm is None or not confirm(preview):
        return {"speak": "Cancelled.", "preview": preview, "confirmed": False}

    # The confirmation screen can change the price; send what was shown.
    price = preview["price"]
    value = round(qty * price, 2)
    return _execute(side, pos["symbol"], qty, price, exchange=c["exchange"],
                    product=pos.get("prd") or "M",
                    did=f"exited the {friendly(pos['symbol'])}", value=value,
                    opening=False, segment="options")


def _opt_type_of(tsym):
    """CE or PE for a held symbol, from the master - any exchange."""
    c = ins.contract_for(tsym)
    return c["option_type"] if c else None


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
