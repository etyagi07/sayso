"""Text-mode agent. Same pipeline the voice layer will use, minus the mic.

Run:  .venv/bin/python -m voice.cli
"""

from voice import safety
from voice.agent import handle

G, R, Y, B, DIM, X = ("\033[92m", "\033[91m", "\033[93m",
                      "\033[94m", "\033[2m", "\033[0m")


def confirm(preview):
    print(f"\n{Y}┌─ CONFIRM ─────────────────────────────────{X}")
    print(f"{Y}│{X}  {preview['action']}  {preview['quantity']} x {preview['symbol']}")
    print(f"{Y}│{X}  limit    {preview['price']:.2f}   (market {preview['ltp']:.2f})")
    print(f"{Y}│{X}  {B}total    {preview['value']:.2f} rupees{X}")
    print(f"{Y}└───────────────────────────────────────────{X}")
    return input(f"  press {G}y{X} to send, anything else to cancel: ").strip().lower() == "y"


def main():
    s = safety.status()
    print(f"\n{G}● LISTENING{X} {DIM}(text mode){X}")
    print(f"{DIM}  options: {', '.join(s['option_allowlist'])} · "
          f"{s['max_lots']} lot max · premium <= {s['max_premium_per_unit']:.0f}{X}")
    print(f"{DIM}  equity : {', '.join(s['allowlist'])} · "
          f"{s['max_order_value']:.0f}/order{X}")
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
