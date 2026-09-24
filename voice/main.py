"""Voice trading agent.

    .venv/bin/python -m voice.main

Press Enter to speak. Whisper runs locally. Orders always require a typed
`y` on screen before anything is sent - speech proposes, you dispose.
"""

from voice import safety
from voice.agent import handle
from voice.cli import confirm
from voice.listen import listen_once, warm_up

G, R, Y, DIM, X = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"


def main():
    s = safety.status()
    print(f"\n{DIM}┄┄┄ voice trading ┄┄┄{X}")
    warm_up()
    print(f"{DIM}  options: {', '.join(s['option_allowlist'])} · "
          f"{s['max_lots']} lot max · premium <= {s['max_premium_per_unit']:.0f} · "
          f"<= {s['max_option_order_value']:,.0f}/order{X}")
    print(f"{DIM}  equity : {', '.join(s['allowlist'])} · "
          f"{s['max_order_value']:.0f}/order{X}")
    print(f"\n{G}● READY{X} {DIM}- press Enter to speak · 't' to type · ctrl-c to quit{X}\n")

    while True:
        try:
            typed = input(f"{G}▶{X} ").strip()
            said = typed if typed and typed != "t" else None
            if said is None:
                if typed == "t":
                    said = input(f"  {DIM}type:{X} ").strip()
                    if not said:
                        continue
                else:
                    said = listen_once()
                    if not said:
                        continue
                    print(f'  {DIM}heard:{X} "{said}"')
            result = handle(said, confirm=confirm)
        except (EOFError, KeyboardInterrupt):
            print(f"\n{DIM}○ stopped{X}")
            return
        except Exception as e:
            print(f"  {R}error: {type(e).__name__}: {e}{X}")
            continue

        colour = R if result.get("blocked") else X
        print(f"  {colour}{result['speak']}{X}\n")


if __name__ == "__main__":
    main()
