"""Check everything this needs, and say exactly what to do about it.

Run: python -m voice.doctor

Written so that someone on a machine nobody can see can paste the output
and get a useful answer. Every failure names its own fix.
"""

from shoonya import profile

profile.from_argv()

import platform
import sys
from pathlib import Path

G, R, Y, DIM, X = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"
ROOT = Path(__file__).resolve().parent.parent

results = []


def check(name, ok, detail="", fix="", blocking=True):
    """blocking=False marks something that limits what you can do rather
    than stopping you - the app still runs."""
    results.append((name, ok, detail, fix, blocking))
    if ok:
        mark = f"{G}ok  {X}"
    else:
        mark = f"{R}FAIL{X}" if blocking else f"{Y}note{X}"
    print(f"  {mark} {name:<26} {DIM}{detail}{X}")
    if not ok and fix:
        for line in fix.splitlines():
            print(f"       {Y}{line}{X}")


def main():
    print(f"\n{DIM}sayso doctor - account: {profile.label()}{X}")
    print(f"  {platform.system()} {platform.release()} · {platform.machine()} "
          f"· python {platform.python_version()}\n")

    v = sys.version_info
    check("python 3.12+", v >= (3, 12), f"{v.major}.{v.minor}.{v.micro}",
          "Install Python 3.12 or newer, then rebuild the environment.")

    try:
        import numpy, sounddevice  # noqa: F401
        check("core packages", True, "numpy, sounddevice")
    except ImportError as e:
        check("core packages", False, str(e),
              "pip install -r requirements.txt")

    try:
        import NorenRestApiPy  # noqa: F401
        check("broker sdk", True, "NorenRestApiPy")
    except ImportError:
        check("broker sdk", False, "missing",
              "pip install -r requirements.txt")

    from voice import config
    backend = config.backend()
    if backend == "mlx":
        try:
            import mlx_whisper  # noqa: F401
            check("speech backend", True, "mlx (apple silicon)")
        except ImportError:
            check("speech backend", False, "mlx-whisper not installed",
                  "pip install -r requirements.txt")
    else:
        try:
            import faster_whisper  # noqa: F401
            check("speech backend", True, "faster-whisper (cpu)")
        except ImportError:
            check("speech backend", False, "faster-whisper not installed",
                  "pip install -r requirements.txt")

    try:
        import sounddevice as sd
        device = sd.query_devices(kind="input")
        check("microphone", True, device["name"])
    except Exception as e:
        check("microphone", False, str(e)[:50],
              "Check a microphone is connected and the OS allows access.\n"
              "macOS: System Settings > Privacy & Security > Microphone,\n"
              "       enable your terminal, then fully quit and reopen it.\n"
              "Windows: Settings > Privacy & security > Microphone.")

    from voice import speak
    audio = speak.works()
    check("spoken readback", audio,
          (f"on, voice {speak._pick_voice() or 'default'}" if audio
           and config.get("speak") else "off in config.json" if audio
           else "no speech engine found"),
          "macOS has 'say' built in; on Windows it uses PowerShell; on\n"
          "Linux install espeak. Orders still work without it - you\n"
          "just read the screen instead.",
          blocking=False)

    from shoonya import network
    state, message = network.check()
    check("internet address", state == "ok", message if state == "ok" else
          {"mismatch": "not registered with this API key",
           "unset": "registered address not saved",
           "unknown": "couldn't look it up"}[state],
          message if state != "ok" else "",
          blocking=state in ("mismatch", "unset"))

    check("microphone calibrated", config.is_calibrated(),
          f"threshold {config.get('silence_rms')}",
          "python -m voice.calibrate")

    session_ok = False
    try:
        from shoonya.client import Shoonya, _session_alive
        api = Shoonya()
        session_ok = api.resume() and _session_alive(api)
        check("broker session", session_ok,
              "logged in" if session_ok else "expired or absent",
              "python -m shoonya.login    (tokens last one trading day)")
    except Exception as e:
        check("broker session", False, f"{type(e).__name__}: {str(e)[:40]}",
              "python -m shoonya.login")

    if session_ok:
        try:
            import shoonya.broker as b
            from shoonya import underlyings
            q = b.quote_checked(*underlyings.get("NIFTY").spot)
            check("market data", bool(q), f"nifty {q['lp']}" if q else "no quote",
                  "The broker returned no data - the market may be closed.")
            uid = getattr(b.api(), "_NorenApi__username", None)
            # An account can have NSE derivatives without BSE's, so the two
            # are checked separately: NFO carries Nifty and Bank Nifty
            # options, BFO carries Sensex.
            for segment, probe, carries in (
                    ("NFO", "NIFTY", "Nifty and Bank Nifty options"),
                    ("BFO", "SENSEX", "Sensex options")):
                res = b._raw_post("/SearchScrip", {"uid": uid, "exch": segment,
                                                   "stext": probe})
                ok = res.get("stat") == "Ok"
                check(f"{segment} segment", ok,
                      "enabled" if ok else res.get("emsg", "")[:40],
                      f"{carries} need the {segment} segment activated "
                      f"with the broker.\nEverything else still works.",
                      blocking=False)
        except Exception as e:
            check("market data", False, f"{type(e).__name__}: {str(e)[:40]}")

    blocking = [r for r in results if not r[1] and r[4]]
    notes = [r for r in results if not r[1] and not r[4]]
    print()
    if blocking:
        print(f"  {R}{len(blocking)} of {len(results)} checks failed{X} - "
              f"fix the highlighted lines above, then run this again.\n")
        return 1

    summary = f"  {G}ready{X} - run: python -m voice.main"
    if notes:
        names = ", ".join(r[0] for r in notes)
        summary += f"\n  {Y}limited:{X} {DIM}{names} - see the note above{X}"
    print(summary + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
