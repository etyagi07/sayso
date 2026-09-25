"""Text-mode agent. Same pipeline the voice layer will use, minus the mic.

Run:  .venv/bin/python -m voice.cli
"""

from voice import safety
from voice.agent import handle

G, R, Y, B, DIM, X = ("\033[92m", "\033[91m", "\033[93m",
                      "\033[94m", "\033[2m", "\033[0m")


def confirm(preview):
    """Show the order and take a decision.

    Orders are priced at market by default. `p` sets an explicit limit
    instead - the screen is where prices get changed, because a number
    said out loud is the least reliable part of a spoken command.
    May edit `preview` in place; the caller reads the price back.
    """
    while True:
        _draw(preview)
        choice = input(f"  {G}y{X} send · {G}p{X} set price · "
                       f"anything else cancels: ").strip().lower()
        if choice == "y":
            return True
        if choice != "p":
            return False
        if not _set_price(preview):
            return False


def _draw(preview):
    depth = ""
    if preview.get("bid") and preview.get("ask"):
        depth = f"   (bid {preview['bid']:.2f} / ask {preview['ask']:.2f})"
    print(f"\n{Y}┌─ CONFIRM ─────────────────────────────────{X}")
    print(f"{Y}│{X}  {preview['action']}  {preview['quantity']} x {preview['symbol']}")
    if preview.get("lots"):
        lots = preview["lots"]
        per_lot = int(preview["quantity"] / lots) if lots else 0
        # Spell out the arithmetic. A misheard "twenty two" instead of
        # "two" is obvious as a lot count long before it is obvious as a
        # rupee total.
        emphasis = R if lots > 1 else X
        print(f"{Y}│{X}  {emphasis}{lots} lot{'s' if lots != 1 else ''}{X}"
              f" x {per_lot} = {preview['quantity']} units{depth}")
    if preview.get("pnl") is not None:
        colour = G if preview["pnl"] >= 0 else R
        sign = "+" if preview["pnl"] >= 0 else ""
        print(f"{Y}│{X}  entry      {preview.get('entry', 0):.2f}"
              f"   now {preview.get('ltp', 0):.2f}"
              f"   {colour}{sign}{preview['pnl']:,.2f}{X}")
    if preview.get("expiry"):
        print(f"{Y}│{X}  {B}expiry     {preview['expiry']}{X}"
              f"   {DIM}strike {preview.get('strike','')}{X}")
    how = "at market" if preview.get("at_market") else "limit"
    print(f"{Y}│{X}  {how:<10} {preview['price']:.2f}"
          f"{'' if preview.get('lots') else depth}")
    big = preview["value"] >= 25000
    money = R if big else B
    print(f"{Y}│{X}  {money}total    {preview['value']:,.2f} rupees{X}"
          f"{'   <<< LARGE ORDER' if big else ''}")
    if preview.get("spoken_price"):
        print(f"{Y}│{X}  {DIM}heard \"{preview['spoken_price']:.2f}\" - "
              f"press p to use it{X}")
    print(f"{Y}└───────────────────────────────────────────{X}")


def _set_price(preview):
    """Take a limit price from the keyboard, validated before it is used."""
    default = preview.get("spoken_price")
    hint = f" [{default:.2f}]" if default else ""
    raw = input(f"  limit price{hint}: ").strip()
    if not raw and default:
        raw = str(default)
    if not raw:
        return False
    try:
        price = float(raw)
    except ValueError:
        print(f"  {R}'{raw}' is not a price.{X}")
        return True

    tick = preview.get("tick") or 0.05
    price = round(round(price / tick) * tick, 2)
    low, high = preview.get("lower_circuit"), preview.get("upper_circuit")
    if low and price < low:
        print(f"  {R}{price:.2f} is below the lower circuit {low:.2f}.{X}")
        return True
    if high and price > high:
        print(f"  {R}{price:.2f} is above the upper circuit {high:.2f}.{X}")
        return True

    preview["price"] = price
    preview["value"] = round(preview["quantity"] * price, 2)
    preview["at_market"] = False
    return True


def main():
    s = safety.status()
    print(f"\n{G}● LISTENING{X} {DIM}(text mode){X}")
    print(f"{DIM}  options: {', '.join(s['option_allowlist'])} · "
          f"{'no lot cap' if s['max_lots'] is None else str(s['max_lots']) + ' lot max'} · "
          f"premium <= {s['max_premium_per_unit']:.0f}{X}")
    print(f"{DIM}  equity : {', '.join(s['allowlist'])} · "
          f"{s['max_order_value']:.0f}/order · all at market{X}")
    print(f"{DIM}  try: 'buy 1 yesbank' · 'what's yesbank at' · 'funds' · "
          f"'what do i own' · ctrl-c to quit{X}\n")

    while True:
        try:
            said = input(f"{G}▶{X} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{DIM}○ stopped{X}")
            return
        if not said:
            continue
        if said in ("quit", "exit"):
            print(f"{DIM}○ stopped{X}")
            return
        try:
            result = handle(said, confirm=confirm)
        except Exception as e:
            print(f"  {R}error: {type(e).__name__}: {e}{X}")
            continue
        colour = R if result.get("blocked") else X
        print(f"  {colour}{result['speak']}{X}")


if __name__ == "__main__":
    main()
