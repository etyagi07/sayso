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
        return {"speak": f"Limit {s['max_order_value']:.0f} rupees per order, "
                         f"{', '.join(s['allowlist'])} only, "
                         f"{s['orders_remaining']} orders left today.", "data": s}
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
