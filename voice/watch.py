"""Keep an eye on a resting order, and say when it fills.

An order that rests is followed briefly when placed; after that it would
fill in silence. This follows it in the background for a few minutes and
announces the outcome, so the chart never has to be left to check.
"""

import threading
import time

import shoonya.broker as b
from voice import config, speak

DIM, G, R, X = "\033[2m", "\033[92m", "\033[91m", "\033[0m"


def follow(order_no, what, every=2.0):
    """Watch `order_no` in the background; announce when it resolves."""
    limit = config.get("follow_seconds") or 300
    thread = threading.Thread(target=_watch, args=(order_no, what, every, limit),
                              daemon=True)
    thread.start()
    return thread


def _watch(order_no, what, every, limit):
    deadline = time.monotonic() + limit
    last_filled = 0
    while time.monotonic() < deadline:
        time.sleep(every)
        state = b.wait_for_outcome(order_no, timeout=0)
        status = state.get("status")
        filled = state.get("filled") or 0
        avg = state.get("avg_fill_price")
        at = f" at {avg:.2f}" if isinstance(avg, (int, float)) else ""
        if status == "COMPLETE":
            _tell(f"{what} filled{at}.", "filled")
            return
        if status in ("REJECTED", "CANCELED"):
            ended = "cancelled" if status == "CANCELED" else "rejected"
            reason = f" {state['reason']}" if state.get("reason") else ""
            if filled:
                # Part of it traded first - that is an open position.
                _tell(f"{what}: {filled} of {state.get('quantity')} filled"
                      f"{at}, the rest was {ended}.{reason}", "partial")
            else:
                _tell(f"{what} was {ended}.{reason}", "rejected")
            return
        if filled > last_filled:
            last_filled = filled
            _tell(f"{what}: {filled} of {state.get('quantity')} filled so far.",
                  "partial")
    _tell(f"{what} is still resting after {limit // 60} minutes. I've "
          f"stopped watching it.", "resting")


def _tell(text, outcome):
    colour = G if outcome == "filled" else (R if outcome == "rejected" else DIM)
    print(f"\n  {colour}> {text}{X}")
    speak.sound(outcome)
    speak.say(text)
