"""Voice trading agent.

    .venv/bin/python -m voice.main

Press Enter to speak. Whisper runs locally. Orders always require a typed
`y` on screen before anything is sent - speech proposes, you dispose.
"""

from shoonya import profile

profile.from_argv()

from shoonya.client import session_user  # noqa: E402
from voice import safety, speak, watch
from voice.agent import friendly, handle
from voice.cli import confirm, limits_line
from voice.listen import listen_once, warm_up

G, R, Y, DIM, X = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"


def banner(s):
    """The account and limits lines shown at start. Kept apart so it can be
    drawn in a test - a renamed field once crashed the app right here."""
    # The ID the session actually belongs to - not a setting that could
    # name a different account from the one orders will go to.
    caps = " · ".join(f"{n} {c} lots" for n, c in s["max_lots"].items())
    return (f"  {Y}account: {session_user() or 'not logged in'} "
            f"({profile.label()}){X}\n"
            f"{DIM}  options: {caps}{X}\n" + limits_line(s))


def main():
    s = safety.status()
    print(f"\n{DIM}┄┄┄ voice trading ┄┄┄{X}")
    warm_up()
    print(banner(s))
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
        speak.announce(result)

        # An order that is still working gets followed, so its fill is
        # announced whenever it lands.
        data = result.get("data") or {}
        if (result.get("outcome") in ("resting", "partial")
                and not result.get("final") and data.get("order_no")):
            what = friendly(data["symbol"]) if data.get("symbol") else "Your order"
            watch.follow(data["order_no"], what)


if __name__ == "__main__":
    main()
