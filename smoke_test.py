"""Read-only checks against the live API. Places no orders."""

import json

from shoonya.client import connect


def show(label, value):
    print(f"\n=== {label} ===")
    print(json.dumps(value, indent=2, default=str)[:1200] if value else value)


api = connect()

show("Limits / margin", api.get_limits())
show("Positions", api.get_positions())
show("Holdings", api.get_holdings(product_type="C"))
show("Order book", api.get_order_book())

# Symbol lookup -> token, which is what quotes and the websocket key off.
hits = api.searchscrip(exchange="NSE", searchtext="RELIANCE")
show("Search: RELIANCE", hits["values"][:3] if hits else None)

quote = api.get_quotes(exchange="NSE", token="2885")  # RELIANCE-EQ
if quote:
    print(f"\n=== Quote ===\n{quote['tsym']}  ltp={quote['lp']}  "
          f"o={quote.get('o')} h={quote.get('h')} l={quote.get('l')} c={quote.get('c')}")

# --- Indices --------------------------------------------------------------
# Index tokens are not the same series as equities, so look them up.
idx = api.searchscrip(exchange="NSE", searchtext="NIFTY INDEX")
show("Search: NIFTY INDEX", idx["values"][:8] if idx else None)

for name, token in [("Nifty 50", "26000"), ("Nifty Bank", "26009")]:
    q = api.get_quotes(exchange="NSE", token=token)
    if q:
        prev, ltp = float(q.get("c", 0)), float(q["lp"])
        chg = ltp - prev
        pct = (chg / prev * 100) if prev else 0
        print(f"\n{q['tsym']:<14} {ltp:>10,.2f}   {chg:+.2f} ({pct:+.2f}%)"
              f"   o={q.get('o')} h={q.get('h')} l={q.get('l')}")
    else:
        print(f"\n{name}: no quote for token {token}")
