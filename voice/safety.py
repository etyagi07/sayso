"""Hard limits enforced in code, regardless of what was said or parsed.

This layer exists because every other layer can be wrong: ASR mishears,
the parser misreads, the LLM hallucinates. These checks are the last thing
between a misunderstanding and a trade, so they are deliberately dumb,
explicit, and fail-closed.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Daily counters must survive a restart, or the caps mean nothing: quitting
# and relaunching would hand you a fresh allowance.
STATE_FILE = Path(__file__).resolve().parent.parent / ".daily_limits.json"


@dataclass
class Limits:
    # Equity testing posture: YESBANK only, tiny size.
    allowlist: set = field(default_factory=lambda: {"YESBANK"})
    max_order_value: float = 100.0
    max_quantity: int = 50
    # Shoonya has no MKT order type; "at market" is a limit priced through
    # the touch. This flag is kept only to name that explicitly.
    allow_marketable_limits: bool = True
    max_orders_per_day: int = 20
    max_value_per_day: float = 500.0

    # --- options -------------------------------------------------------
    # One Nifty lot is 65 units, so an ATM premium near 125 costs ~8,100.
    # These cap the damage from a misheard command; they are NOT a view on
    # what is a sensible trade.
    option_allowlist: set = field(default_factory=lambda: {"NIFTY"})
    # 10 lots is the ceiling. There is no per-order value cap, so this is
    # what stops a misheard number becoming a huge position - "two" and
    # "twenty two" are one slip apart in speech.
    max_lots: int = 10
    max_premium_per_unit: float = 200.0
    # No per-order value cap: the client sizes his own trades. What is
    # left between a misheard number and a filled position is the
    # confirmation screen - which is why it spells out lots, units and
    # total rather than just a price.
    max_option_order_value: float = None
    max_option_orders_per_day: int = 10


LIMITS = Limits()

def _blank(date_=None):
    return {"date": date_,
            "equity": {"orders": 0, "value": 0.0},
            "options": {"orders": 0, "value": 0.0}}


def _load():
    """Counters are per-segment: equity and options have very different
    caps, so one pot means a single option order exhausts a whole day of
    equity allowance."""
    try:
        data = json.loads(STATE_FILE.read_text())
        state = _blank(data.get("date"))
        for seg in ("equity", "options"):
            row = data.get(seg) or {}
            state[seg] = {"orders": int(row.get("orders", 0)),
                          "value": float(row.get("value", 0.0))}
        return state
    except (OSError, ValueError, TypeError):
        return _blank()


def _save(state):
    try:
        STATE_FILE.write_text(json.dumps({**state, "date": str(state["date"])}))
    except OSError:
        pass  # Never let bookkeeping block a legitimate trade.


_spent_today = _load()


class Rejected(Exception):
    """A limit said no. The message is meant to be read aloud."""


def _today_counters(segment="equity"):
    today = str(date.today())
    if _spent_today["date"] != today:
        _spent_today.update(_blank(today))
        _save(_spent_today)
    return _spent_today[segment]


def check(symbol, quantity, price, price_type, limits=LIMITS):
    """Raise Rejected if this order breaches any limit. Returns the value."""
    base = symbol.split("-")[0].upper()

    if limits.allowlist and base not in limits.allowlist:
        raise Rejected(
            f"{base} is not on the allowlist. Only "
            f"{', '.join(sorted(limits.allowlist))} can be traded right now."
        )

    if price_type == "MKT":
        raise Rejected("Shoonya does not accept market orders; "
                       "this should have been priced as a limit.")

    if quantity <= 0:
        raise Rejected(f"Quantity {quantity} is not a valid order size.")
    if quantity > limits.max_quantity:
        raise Rejected(
            f"Quantity {quantity} exceeds the per-order cap of "
            f"{limits.max_quantity} shares."
        )

    if price is None:
        raise Rejected("No price available to value this order against.")

    value = round(quantity * price, 2)
    if value > limits.max_order_value:
        raise Rejected(
            f"That order is worth {value:.2f} rupees, over the "
            f"{limits.max_order_value:.0f} rupee per-order limit."
        )

    counters = _today_counters("equity")
    if counters["orders"] >= limits.max_orders_per_day:
        raise Rejected(
            f"Daily order limit reached ({limits.max_orders_per_day} orders)."
        )
    if counters["value"] + value > limits.max_value_per_day:
        raise Rejected(
            f"That would take today's traded value to "
            f"{counters['value'] + value:.2f}, over the "
            f"{limits.max_value_per_day:.0f} rupee daily cap."
        )

    return value


def check_option(tsym, underlying, lots, lot_size, price, limits=LIMITS):
    """Limits for an option order. Returns (total_value, units)."""
    if limits.option_allowlist and underlying.upper() not in limits.option_allowlist:
        raise Rejected(
            f"{underlying} options are not on the allowlist. Only "
            f"{', '.join(sorted(limits.option_allowlist))} is permitted."
        )
    if lots <= 0:
        raise Rejected(f"{lots} lots is not a valid order size.")
    if limits.max_lots is not None and lots > limits.max_lots:
        raise Rejected(
            f"{lots} lots exceeds the cap of {limits.max_lots} lot"
            f"{'s' if limits.max_lots != 1 else ''} per order."
        )
    if price is None:
        raise Rejected("No price available to value this option against.")
    if price > limits.max_premium_per_unit:
        raise Rejected(
            f"Premium {price:.2f} is above the {limits.max_premium_per_unit:.0f} "
            f"per-unit limit - that contract is too expensive to trade here."
        )

    units = lots * lot_size
    value = round(units * price, 2)
    if (limits.max_option_order_value is not None
            and value > limits.max_option_order_value):
        raise Rejected(
            f"That order is worth {value:,.2f} rupees, over the "
            f"{limits.max_option_order_value:,.0f} rupee option limit."
        )

    counters = _today_counters("options")
    if counters["orders"] >= limits.max_option_orders_per_day:
        raise Rejected(
            f"Daily option order limit reached "
            f"({limits.max_option_orders_per_day} orders)."
        )
    return value, units


def record(value, segment="equity"):
    """Count an order that actually reached the market.

    Rejected orders must not count - they consumed no capital, and an
    allowance spent on trades that never happened is just lost capacity.
    """
    counters = _today_counters(segment)
    counters["orders"] += 1
    counters["value"] = round(counters["value"] + value, 2)
    _save(_spent_today)


def status():
    eq, op = _today_counters("equity"), _today_counters("options")
    return {
        "allowlist": sorted(LIMITS.allowlist),
        "max_order_value": LIMITS.max_order_value,
        "marketable_limits": LIMITS.allow_marketable_limits,
        "orders_today": eq["orders"],
        "value_today": eq["value"],
        "orders_remaining": LIMITS.max_orders_per_day - eq["orders"],
        "option_orders_today": op["orders"],
        "option_value_today": op["value"],
        "option_orders_remaining": (LIMITS.max_option_orders_per_day
                                    - op["orders"]),
        "option_allowlist": sorted(LIMITS.option_allowlist),
        "max_lots": LIMITS.max_lots,   # None = no cap
        "max_premium_per_unit": LIMITS.max_premium_per_unit,
        "max_option_order_value": LIMITS.max_option_order_value,
    }
