"""Intent -> resolved order -> typed confirmation -> execution.

The confirmation step is the point of the whole design. Everything upstream
(ASR, parser) can be wrong, so nothing goes live until a human has read the
resolved instrument, the price, and the total cost on screen and pressed y.
"""

import time

import shoonya.broker as b
from shoonya import instruments as ins
from shoonya import underlyings
from voice import numbers, safety, stocks, strikes
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
    elif pending and intent["intent"] == "number_answer" \
            and pending["missing"] == "quantity":
        spans = intent["number_spans"]
        qty, used = numbers.parse(spans[0]) if len(spans) == 1 else (None, 0)
        if qty is None or used != len(spans[0]) or not float(qty).is_integer():
            return {"speak": "I didn't catch a quantity. Say the whole order "
                             "again.", "blocked": True}
        intent = {**pending["intent"], "quantity": int(qty)}
    elif pending and intent["intent"] == "product_answer" \
            and pending["missing"] == "product":
        intent = {**pending["intent"], "product": intent["product"]}
    elif intent["intent"] in ("index_answer", "number_answer",
                              "product_answer"):
        return {"speak": "I wasn't waiting for an answer. Say the whole "
                         "command.", "blocked": True}

    kind = intent["intent"]

    if kind == "cancel":
        return {"speak": "OK, nothing done.", "cancelled": True}
    if kind == "cancel_order_unsupported":
        return {"speak": "Cancelling an order by voice isn't supported yet. "
                         "Use the broker's app for that one.", "blocked": True}
    if kind == "question":
        return {"speak": "That sounded like a question, so I haven't done "
                         "anything. Say it as a command to trade.",
                "blocked": True}
    if kind == "negated":
        return {"speak": "You said not to, so I haven't done anything.",
                "blocked": True}
    if kind == "option_ambiguous":
        return {"speak": "I heard both call and put. Say just one.",
                "blocked": True, "needs_clarification": True}
    # Speech that could mean two different trades. Each of these was once
    # read as one of them - and a confident wrong trade is the worst
    # outcome this program has.
    if kind in UNCLEAR:
        return {"speak": UNCLEAR[kind](intent) + " I haven't done anything.",
                "blocked": True, "needs_clarification": True}

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
                "intent": intent, "blocked": True}
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
                          f"left today. Equity: {s['stocks']} stocks, up to "
                          f"{s['max_order_value']:,.0f} rupees per order."),
                "data": s}
    if kind == "quote":
        stock, problem = _stock(intent["name"])
        if problem:
            return problem
        q = b.quote_checked("NSE", stock["token"], expect_tsym=stock["tsym"])
        if not q:
            return {"speak": f"I couldn't get a reliable price for "
                             f"{stock['company']}.", "blocked": True}
        q = {"symbol": stock["tsym"], "company": stock["company"],
             "ltp": b._f(q.get("lp")),
             "prev_close": b._f(q.get("c")), "change_pct": b._f(q.get("pc"))}
        # change_pct is absent for some instruments - compute from prev close.
        pct = q.get("change_pct")
        if pct is None and q.get("prev_close"):
            pct = (q["ltp"] - q["prev_close"]) / q["prev_close"] * 100
        move = f", {pct:+.2f} percent" if pct is not None else ""
        name = q.get("company") or q["symbol"].replace("-EQ", "")
        return {"speak": f"{name} is at {q['ltp']:.2f}{move}.", "data": q}

    if kind in ("option_buy", "option_exit", "option_quote", "option_refused"):
        return _handle_option(intent, confirm)

    if kind != "order":
        return {"speak": "I'm not sure what to do with that.", "intent": intent,
                "blocked": True}

    # --- equity order -----------------------------------------------------
    return _equity_order(intent, confirm)


PRODUCT_NAMES = {"I": "intraday", "C": "delivery", "M": "margin"}

UNCLEAR = {
    "side_ambiguous": lambda i: "I heard both buy and sell.",
    "index_ambiguous": lambda i: (
        "I heard " + " and ".join(underlyings.get(n).spoken
                                  for n in i["indices"]) + ". Say one index."),
    "unclear_correction": lambda i: (
        "You changed your mind partway through, so say the whole command "
        "again."),
    "number_unclear": lambda i: f"I couldn't read the number in \"{i['heard']}\".",
    "quantity_ambiguous": lambda i: (
        f"I heard two quantities, {i['quantities'][0]:g} and "
        f"{i['quantities'][1]:g}. Say one."),
}


def _stock(name):
    """A spoken company name -> one stock, or something to say instead."""
    found = stocks.resolve(name or "")
    if found is None:
        return None, {"speak": f"I don't know {name}. I can trade Nifty 50 "
                               f"stocks - say the company's name.",
                      "blocked": True}
    if "ambiguous" in found:
        options = [company for _, company in found["ambiguous"]]
        listed = ", ".join(options[:-1]) + " or " + options[-1]
        return None, {"speak": f"{name.title()} could be {listed}. Say the "
                               f"full name.", "blocked": True,
                      "needs_clarification": True}
    return found, None


def _equity_order(intent, confirm):
    stock, problem = _stock(intent.get("name"))
    if problem:
        return problem
    tsym, company = stock["tsym"], stock["company"]

    q = b.quote_checked("NSE", stock["token"], expect_tsym=tsym)
    if not q:
        return {"speak": f"I couldn't get a reliable price for {company}.",
                "blocked": True}
    ltp = b._f(q.get("lp"))
    side_word = "buy" if intent["side"] == "B" else "sell"
    opening = intent["side"] == "B"
    quantity = intent["quantity"]

    if not opening:
        # A sell closes something you hold. Selling more than that would
        # be opening a short - refused, as selling options to open is.
        position = next((p for p in b.positions()
                         if p["symbol"] == tsym and p["qty"] > 0), None)
        if position is None:
            return {"speak": f"You don't hold any {company} to sell.",
                    "blocked": True}
        held = position["qty"]
        if quantity is None:
            quantity = held
        elif quantity > held:
            return {"speak": f"You hold {held} {company}. I won't sell more "
                             f"than you hold.", "blocked": True}
        # An exit has to use the product the position was opened with.
        product = position.get("prd") or "C"
    else:
        if quantity is None:
            _set_pending(intent, "quantity")
            return {"speak": f"How many {company} shares?", "blocked": True,
                    "needs_answer": "quantity"}
        product = intent.get("product")
        if product is None:
            _set_pending(intent, "product")
            return {"speak": "Intraday or delivery?", "blocked": True,
                    "needs_answer": "product"}

    # Everything goes at market: a limit priced through the touch so it
    # fills now. A price said out loud is shown on the confirmation screen
    # rather than executed - numbers are the least reliable thing in speech.
    spoken_price = intent.get("price")
    view = b.quote_view(q)
    price = b.marketable_price(intent["side"], view)
    if price is None:
        return {"speak": f"No usable price for {company}.", "blocked": True}

    value = round(quantity * price, 2)
    if opening:
        # Limits apply to opening a position. Closing one is never blocked -
        # a cap that stops you exiting traps you in the trade.
        try:
            value = safety.check(tsym, quantity, price, "LMT")
        except safety.Rejected as e:
            return {"speak": str(e), "blocked": True}

    kind = PRODUCT_NAMES.get(product, product)
    preview = {
        "action": f"{side_word.upper()} ({kind})", "symbol": tsym,
        "company": company, "quantity": quantity, "product": kind,
        "price": price, "value": value, "ltp": ltp, "at_market": True,
        "spoken_price": spoken_price,
        "say": (f"{side_word.capitalize()} {quantity} {company}, {kind}, "
                f"about {value:,.0f} rupees."),
        "bid": view["bid"], "ask": view["ask"], "tick": view["tick"],
        "lower_circuit": view["lower_circuit"],
        "upper_circuit": view["upper_circuit"],
        "spoken": (f"{side_word} {quantity} {company} ({kind}) at market, "
                   f"about {price:.2f}, total {value:,.2f} rupees."),
    }
    if confirm is None or not confirm(preview):
        return {"speak": "Cancelled.", "preview": preview, "confirmed": False}

    # The confirmation screen can change the price; send what was shown.
    price = preview["price"]
    value = round(quantity * price, 2)
    if opening:
        try:
            value = safety.check(tsym, quantity, price, "LMT")
        except safety.Rejected as e:
            return {"speak": str(e), "blocked": True}

    return _execute(intent["side"], tsym, quantity, price, exchange="NSE",
                    product=product,
                    did=f"{'bought' if opening else 'sold'} {quantity} {company}",
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


AT_MARKET = ("Options go at market, so I don't take a price - I heard "
             "\"at {heard}\". Say the strike, or leave it out for "
             "at-the-money.")


def _read_numbers(intent, name):
    """Quantity and strike from the spoken numbers, on this index's ladder."""
    ladder, spot, expiry = _ladder_and_spot(name)
    if ladder is None:
        return None, None, None, {"speak": f"Could not read the "
                                           f"{underlyings.get(name).spoken} "
                                           f"level.", "blocked": True}
    spans = list(intent.get("number_spans") or [])
    # "buy call at 23100" names a strike. "buy call at 85" names a price -
    # which options do not take - and must never be read as 85 lots.
    for span in intent.get("price_spans") or []:
        found, _ = strikes.resolve(span, ladder, spot, underlyings.band(name))
        if found is None:
            return None, None, None, {
                "speak": AT_MARKET.format(heard=" ".join(span)),
                "blocked": True, "needs_clarification": True}
        spans.append(span)
    lots, strike, err = strikes.read_order(spans, ladder, spot,
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
        # What is read aloud: just enough to catch a wrong index, strike,
        # side or size. The screen carries the rest.
        "say": (f"Buy {lots} lot{plural}, {u.spoken} {c['strike']} {word}, "
                f"{_when(c)}, about {value:,.0f} rupees."),
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
        return {"speak": f"You have no open {where}{word} position.",
                "blocked": True}

    # Numbers said with an exit: a strike, a number of lots, or both - read
    # the same way as a buy, but against the strikes actually held, so
    # nothing can match a position that was not named.
    spans = list(intent.get("number_spans") or [])
    at = intent.get("price_spans") or []
    lots = None
    if spans or at:
        held_strikes = {c["strike"] for _, c in held}
        for span in at:
            found, _ = strikes.resolve(span, held_strikes, 0, float("inf"))
            if found is None:
                return {"speak": AT_MARKET.format(heard=" ".join(span))
                        .replace("Options go", "Exits go"),
                        "blocked": True, "needs_clarification": True}
        lots, strike, err = strikes.read_order(spans + at, held_strikes, 0,
                                               band=float("inf"))
        if err:
            heard = " ".join(" ".join(sp) for sp in spans + at)
            return {"speak": f"You don't hold a {where}{word} at {heard}.",
                    "blocked": True, "needs_clarification": True}
        if strike is not None:
            held = [(p, c) for p, c in held if c["strike"] == strike]

    if len(held) > 1 and len({(c["underlying"], c["strike"])
                              for _, c in held}) == 1:
        # One strike, two expiries. Asking "which strike?" again would
        # loop forever - the strike is not what differs.
        listed = " and ".join(friendly(p["symbol"]) for p, _ in held)
        return {"speak": f"You hold {listed} - the same strike in two "
                         f"expiries. Exit that one in the broker's app for "
                         f"now.", "blocked": True}

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
    lot = c.get("lot") or 1
    if lots is not None:
        if lots * lot > qty:
            have = (f"{qty // lot} lot{'s' if qty // lot != 1 else ''}"
                    if qty % lot == 0 else f"{qty} units")
            return {"speak": f"You hold {have} of the "
                             f"{friendly(pos['symbol'])}. I won't exit more "
                             f"than you hold.", "blocked": True}
        qty = lots * lot
    part = (f"{lots} lot{'s' if lots != 1 else ''} of " if lots is not None
            and qty < abs(pos["qty"]) else "")
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
    if price is None:
        return {"speak": f"No usable price for {friendly(pos['symbol'])}.",
                "blocked": True}
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
        "say": (f"Exit {part}{friendly(pos['symbol'])}, about "
                f"{value:,.0f} rupees." + result_words),
        "bid": view["bid"], "ask": view["ask"], "tick": view["tick"],
        "lower_circuit": view["lower_circuit"],
        "upper_circuit": view["upper_circuit"],
        "spoken": (f"exit {part or 'your '}{friendly(pos['symbol'])}, "
                   f"{qty} units at market, about {value:,.0f} rupees."
                   + (f" Entry was {pos['avg_price']:.2f}, now {ltp:.2f}."
                      if pos.get("avg_price") and ltp is not None else "")
                   + result_words),
    }
    if confirm is None or not confirm(preview):
        return {"speak": "Cancelled.", "preview": preview, "confirmed": False}

    # The confirmation screen can change the price; send what was shown.
    price = preview["price"]
    value = round(qty * price, 2)
    return _execute(side, pos["symbol"], qty, price, exchange=c["exchange"],
                    product=pos.get("prd") or "M",
                    did=f"exited {part or 'the '}{friendly(pos['symbol'])}",
                    value=value,
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

    filled, total = state.get("filled") or 0, state.get("quantity") or quantity
    avg = state.get("avg_fill_price")
    avg = f"{avg:.2f}" if isinstance(avg, (int, float)) else avg
    at = f" at {avg}" if avg else ""

    if status in ("REJECTED", "CANCELED"):
        ended = "cancelled" if status == "CANCELED" else "rejected"
        reason = state.get("reason")
        if filled:
            # Some of it traded before the rest was stopped. That is a
            # position, not a rejection - saying "rejected" hides it.
            if opening:
                safety.record(round(value * filled / total, 2), segment)
            return {"speak": f"Part filled: {filled} of {total}{at}. The "
                             f"rest was {ended}."
                             + (f" {reason}" if reason else ""),
                    "outcome": "partial", "final": True, "confirmed": True,
                    "data": {**sent, "state": state}}
        if status == "CANCELED":
            return {"speak": "The order was cancelled before it filled."
                             + (f" {reason}" if reason else ""),
                    "outcome": "rejected", "data": {**sent, "state": state}}
        return {"speak": f"Rejected by the exchange. {reason or ''}".strip(),
                "outcome": "rejected", "data": {**sent, "state": state}}

    # It reached the market. Only opening trades count against the caps.
    if opening:
        safety.record(value, segment)

    if status == "COMPLETE":
        return {"speak": f"Filled. {did[:1].upper()}{did[1:]}{at}.",
                "outcome": "filled",
                "confirmed": True, "data": {**sent, "state": state}}
    if filled and filled < total:
        return {"speak": f"Part filled: {filled} of {total}{at}. The "
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
