"""Intent -> resolved order -> typed confirmation -> execution.

The confirmation step is the point of the whole design. Everything upstream
(ASR, parser) can be wrong, so nothing goes live until a human has read the
resolved instrument, the price, and the total cost on screen and pressed y.
"""

import shoonya.broker as b
from voice import safety
from voice.parser import parse


def handle(transcript, confirm=None):
    """Run one utterance. `confirm` takes a preview dict, returns bool."""
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
        book = b.api().get_order_book() or []
        live = [o for o in book if o.get("status") in ("OPEN", "TRIGGER_PENDING")]
        return {"speak": f"{len(live)} open of {len(book)} orders today.",
                "data": live}
    if kind == "limits":
        s = safety.status()
        return {"speak": (f"Options: {', '.join(s['option_allowlist'])} only, "
                          f"{s['max_lots']} lot maximum, premium up to "
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

    q = b.api().get_quotes(exchange="NSE", token=sym["token"]) or {}
    ltp = b._f(q.get("lp"))
    side_word = "buy" if intent["side"] == "B" else "sell"

    quantity = intent["quantity"]
    if quantity is None:
        if intent["side"] == "S":
            held = next((p["qty"] for p in b.positions()
                         if p["symbol"] == sym["tsym"]), 0)
            if not held:
                return {"speak": f"You don't hold any {sym['tsym']} to sell."}
            quantity = held
        else:
            return {"speak": f"How many {sym['tsym']} do you want to {side_word}?",
                    "needs_quantity": True, "data": sym}

    # Price against live depth: cross the spread so it actually fills.
    price = intent["price"]
    if price is None:
        book_side = "sp1" if intent["side"] == "B" else "bp1"
        price = b._f(q.get(book_side)) or ltp

    try:
        value = safety.check(sym["tsym"], quantity, price, "LMT")
    except safety.Rejected as e:
        return {"speak": str(e), "blocked": True}

    preview = {
        "action": side_word.upper(), "symbol": sym["tsym"], "quantity": quantity,
        "price": price, "value": value, "ltp": ltp,
        "spoken": (f"{side_word} {quantity} {sym['tsym'].replace('-EQ','')} "
                   f"at {price:.2f}, total {value:.2f} rupees. "
                   f"Market is {ltp:.2f}."),
    }

    if confirm is None or not confirm(preview):
        return {"speak": "Cancelled.", "preview": preview, "confirmed": False}

    result = b.place_and_confirm(intent["side"], sym["tsym"], quantity,
                                 price=price, live=True)
    if result.get("status") == "REJECTED":
        return {"speak": f"Rejected. {result.get('reason','')}", "data": result}

    safety.record(value)
    outcome = result.get("outcome", {})
    if outcome.get("status") == "COMPLETE":
        spoken = (f"Done. {side_word} {outcome['filled']} "
                  f"{sym['tsym'].replace('-EQ','')} at {outcome['avg_fill_price']}.")
    else:
        spoken = f"Order is {outcome.get('status','pending')}, not filled yet."
    return {"speak": spoken, "data": result, "confirmed": True}


# --- options ---------------------------------------------------------------

_SPOKEN = {"CE": "call", "PE": "put"}


def _handle_option(intent, confirm):
    kind = intent["intent"]
    opt = intent["option_type"]
    word = _SPOKEN[opt]

    if kind == "option_refused":
        return {"speak": intent["reason"], "blocked": True}

    if kind == "option_exit":
        return _exit_option(opt, confirm)

    c = b.option_contract(opt)
    if "error" in c:
        return {"speak": c["error"], "blocked": True}

    if kind == "option_quote":
        return {"speak": f"The {c['strike']} {word} is at {c['ltp']:.2f}, "
                         f"Nifty at {c['spot']:.0f}.", "data": c}

    # --- buy to open ---------------------------------------------------
    lots = int(intent.get("lots") or 1)
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
        "spoken": (f"buy {lots} lot of the {c['strike']} {word}, "
                   f"{units} units at {price:.2f}, "
                   f"total {value:,.0f} rupees. Nifty at {c['spot']:.0f}."
                   + adjusted),
    }
    if confirm is None or not confirm(preview):
        return {"speak": "Cancelled.", "preview": preview, "confirmed": False}

    result = b.place_direct_and_confirm("B", c["tsym"], units, price,
                                        exchange="NFO", product="M")
    return _report(result, f"bought {lots} lot of the {c['strike']} {word}",
                   value)


def _exit_option(opt, confirm):
    word = _SPOKEN[opt]
    held = [p for p in b.positions()
            if p["symbol"].startswith("NIFTY")
            and _opt_type_of(p["symbol"]) == opt and p["qty"] != 0]
    if not held:
        return {"speak": f"You have no open {word} position."}

    pos = held[0]
    qty = abs(pos["qty"])
    q = b.quote_checked("NFO", _token_for(pos["symbol"]), pos["symbol"])
    if not q:
        return {"speak": f"Could not price {pos['symbol']} to exit it.",
                "blocked": True}

    side = "S" if pos["qty"] > 0 else "B"
    price = b.marketable_price(side, {
        "tick": b._f(q.get("ti")) or 0.05,
        "bid": b._f(q.get("bp1")), "ask": b._f(q.get("sp1")),
        "ltp": b._f(q.get("lp")),
        "lower_circuit": b._f(q.get("lc")), "upper_circuit": b._f(q.get("uc")),
    })
    value = round(qty * price, 2)
    preview = {
        "action": "EXIT " + ("SELL" if side == "S" else "BUY"),
        "symbol": pos["symbol"], "quantity": qty, "lots": None,
        "price": price, "value": value, "ltp": b._f(q.get("lp")),
        "spoken": (f"exit your {word} position, {qty} units at {price:.2f}, "
                   f"about {value:,.0f} rupees. "
                   f"Entry was {pos['avg_price']:.2f}, "
                   f"now {b._f(q.get('lp')):.2f}."),
    }
    if confirm is None or not confirm(preview):
        return {"speak": "Cancelled.", "preview": preview, "confirmed": False}

    result = b.place_direct_and_confirm(side, pos["symbol"], qty, price,
                                        exchange="NFO", product="M")
    return _report(result, f"exited the {word}", value)


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


def _report(result, did, value):
    if result.get("status") == "REJECTED":
        return {"speak": f"Rejected. {result.get('reason','')}", "data": result}
    safety.record(value)
    outcome = result.get("outcome", {})
    if outcome.get("status") == "COMPLETE":
        return {"speak": f"Done, {did} at {outcome.get('avg_fill_price')}.",
                "data": result, "confirmed": True}
    return {"speak": f"Order is {outcome.get('status','pending')}, not filled yet.",
            "data": result, "confirmed": True}
