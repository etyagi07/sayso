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
    # Testing posture, 2026-09-23: YESBANK only, tiny size.
    allowlist: set = field(default_factory=lambda: {"YESBANK"})
    max_order_value: float = 100.0
    max_quantity: int = 50
    allow_market_orders: bool = False
    max_orders_per_day: int = 20
    max_value_per_day: float = 500.0


LIMITS = Limits()

def _load():
    try:
        data = json.loads(STATE_FILE.read_text())
        return {"date": data.get("date"), "orders": int(data.get("orders", 0)),
                "value": float(data.get("value", 0.0))}
    except (OSError, ValueError, TypeError):
        return {"date": None, "orders": 0, "value": 0.0}


def _save(state):
    try:
        STATE_FILE.write_text(json.dumps({**state, "date": str(state["date"])}))
    except OSError:
        pass  # Never let bookkeeping block a legitimate trade.


_spent_today = _load()


class Rejected(Exception):
    """A limit said no. The message is meant to be read aloud."""


def _today_counters():
    today = str(date.today())
    if _spent_today["date"] != today:
        _spent_today.update({"date": today, "orders": 0, "value": 0.0})
        _save(_spent_today)
    return _spent_today


def check(symbol, quantity, price, price_type, limits=LIMITS):
    """Raise Rejected if this order breaches any limit. Returns the value."""
    base = symbol.split("-")[0].upper()

    if limits.allowlist and base not in limits.allowlist:
        raise Rejected(
            f"{base} is not on the allowlist. Only "
            f"{', '.join(sorted(limits.allowlist))} can be traded right now."
        )

    if price_type == "MKT" and not limits.allow_market_orders:
        raise Rejected("Market orders are disabled. Use a limit price.")

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

    counters = _today_counters()
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


def record(value):
    """Count an order that actually went out."""
    counters = _today_counters()
    counters["orders"] += 1
    counters["value"] = round(counters["value"] + value, 2)
    _save(counters)


def status():
    c = _today_counters()
    return {
        "allowlist": sorted(LIMITS.allowlist),
        "max_order_value": LIMITS.max_order_value,
        "market_orders": LIMITS.allow_market_orders,
        "orders_today": c["orders"],
        "value_today": c["value"],
        "orders_remaining": LIMITS.max_orders_per_day - c["orders"],
    }
