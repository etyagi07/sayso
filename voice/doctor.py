"""Check everything this needs, and say exactly what to do about it.

Run: python -m voice.doctor

Written so that someone on a machine nobody can see can paste the output
and get a useful answer. Every failure names its own fix.
"""

import platform
import sys
from pathlib import Path

G, R, Y, DIM, X = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"
ROOT = Path(__file__).resolve().parent.parent

results = []


def check(name, ok, detail="", fix=""):
    results.append((name, ok, detail, fix))
    mark = f"{G}ok  {X}" if ok else f"{R}FAIL{X}"
    print(f"  {mark} {name:<26} {DIM}{detail}{X}")
    if not ok and fix:
        for line in fix.splitlines():
            print(f"       {Y}{line}{X}")


def main():
    print(f"\n{DIM}sayso doctor{X}")
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

    check("microphone calibrated", config.is_calibrated(),
          f"threshold {config.get('silence_rms')}",
          "python -m voice.calibrate")

    env = ROOT / ".env"
    if not env.exists():
        check("credentials", False, "not set",
              "python -m shoonya.credentials")
    else:
        text = env.read_text()
        filled = all(f"{k}=" in text and text.split(f"{k}=")[1].split("\n")[0].strip()
                     for k in ("SHOONYA_CLIENT_ID", "SHOONYA_USER_ID",
                               "SHOONYA_SECRET_CODE"))
        check("credentials", filled, ".env" if filled else "a field is empty",
              "python -m shoonya.credentials")

    session_ok = False
    try:
        from shoonya.client import Shoonya, _session_alive
        api = Shoonya()
        session_ok = api.resume() and _session_alive(api)
        check("broker session", session_ok,
              "logged in" if session_ok else "expired or absent",
              "python -m shoonya.login    (tokens last one trading day)")
    except SystemExit:
        check("broker session", False, "credentials not loaded",
              "Fill in .env first, then: python -m shoonya.login")
    except Exception as e:
        check("broker session", False, f"{type(e).__name__}: {str(e)[:40]}",
              "python -m shoonya.login")

    if session_ok:
        try:
            import shoonya.broker as b
            q = b.quote_checked("NSE", "26000")
            check("market data", bool(q), f"nifty {q['lp']}" if q else "no quote",
                  "The broker returned no data - the market may be closed.")
            uid = getattr(b.api(), "_NorenApi__username", None)
            res = b._raw_post("/SearchScrip",
                              {"uid": uid, "exch": "NFO", "stext": "NIFTY"})
            fo = res.get("stat") == "Ok"
            check("f&o segment", fo,
                  "enabled" if fo else res.get("emsg", "")[:40],
                  "Options need the F&O segment activated with the broker.\n"
                  "Equity still works without it.")
        except Exception as e:
            check("market data", False, f"{type(e).__name__}: {str(e)[:40]}")

    failed = [r for r in results if not r[1]]
    print()
    if failed:
        print(f"  {R}{len(failed)} of {len(results)} checks failed{X} - "
              f"fix the highlighted lines above, then run this again.\n")
        return 1
    print(f"  {G}all {len(results)} checks passed{X} - "
          f"run: python -m voice.main\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
