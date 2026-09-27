"""Spoken readback and outcome sounds, so eyes can stay on the chart.

Two guarantees shape this module:

- It never talks over the microphone. Pressing to talk stops any speech or
  sound first - push-to-talk, the way a radio works - and anything
  announced while recording waits until it stops. Otherwise the app's own
  readback - "buy one lot of the Nifty call" - could be heard as a command.
- It never blocks the confirmation screen. Speech runs on its own thread;
  the box appears at once and the voice reads it out alongside.

Uses what the operating system already has: `say` and system sounds on
macOS, SAPI through PowerShell on Windows, espeak on Linux. No extra
dependencies, and if none is available it stays silent rather than failing.
"""

import platform
import queue
import re
import shutil
import subprocess
import threading
import time

from voice import config

SYSTEM = platform.system()

# Outcome -> sound. Three to learn: filled, waiting, rejected. Everything
# else - part filled, unknown, a question, a refusal - shares one sound that
# means "listen to the words". Few enough to know by ear mid-trade.
_MAC = "/System/Library/Sounds/"
_LISTEN = {"Darwin": _MAC + "Pop.aiff", "Windows": "Question"}
SOUNDS = {
    "Darwin": {
        "filled": _MAC + "Glass.aiff",       # bright - done
        "resting": _MAC + "Tink.aiff",       # waiting to fill
        "rejected": _MAC + "Basso.aiff",     # low - broker said no
        **{o: _LISTEN["Darwin"]
           for o in ("partial", "unknown", "question", "blocked")},
    },
    "Windows": {
        "filled": "Asterisk", "resting": "Exclamation", "rejected": "Hand",
        **{o: _LISTEN["Windows"]
           for o in ("partial", "unknown", "question", "blocked")},
    },
}

MONTHS = {"Jan": "January", "Feb": "February", "Mar": "March",
          "Apr": "April", "Jun": "June", "Jul": "July", "Aug": "August",
          "Sep": "September", "Oct": "October", "Nov": "November",
          "Dec": "December"}

_queue = queue.Queue()
_idle = threading.Event()
_idle.set()
_mic_open = threading.Event()
_worker = None
_voice = None
_current = None           # the speech or sound process playing now, if any
_running = False          # the worker is part-way through an item
_lock = threading.Lock()


def enabled():
    return bool(config.get("speak")) and _available()


def _available():
    if SYSTEM == "Darwin":
        return bool(shutil.which("say"))
    if SYSTEM == "Windows":
        return bool(shutil.which("powershell"))
    return bool(shutil.which("espeak") or shutil.which("spd-say"))


def _pick_voice():
    """An Indian English voice if the machine has one - it says 'Nifty' and
    'Sensex' the way the user does. Otherwise the system default."""
    chosen = config.get("voice")
    if chosen or SYSTEM != "Darwin":
        return chosen
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    # Lines look like "Rishi   en_IN   # Hello..." or, for enhanced voices,
    # "Aman (English (India)) en_IN   # Hello...". Take the name before the
    # locale, whatever the spacing.
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s+([a-z]{2}_[A-Z]{2})\s+#", line)
        if m and m.group(2) == "en_IN":
            return m.group(1).strip()
    return None


def works():
    """Actually produce speech, silently, to prove the engine and voice
    are usable - checking the binary exists says nothing about the voice."""
    if not _available():
        return False
    if SYSTEM != "Darwin":
        return True
    import tempfile
    voice = _pick_voice()
    with tempfile.NamedTemporaryFile(suffix=".aiff") as f:
        cmd = ["say", "-o", f.name] + (["-v", voice] if voice else []) + ["ok"]
        try:
            return subprocess.run(cmd, capture_output=True,
                                  timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False


def for_speech(text):
    """Turn screen text into something worth listening to."""
    # Long digit runs are order numbers - unreadable aloud, and on screen.
    text = re.sub(r"\b[Oo]rder \d{8,}\b", "The order", text)
    text = re.sub(r"\b\d{10,}\b", "", text)
    for short, full in MONTHS.items():
        text = re.sub(rf"\b(\d{{1,2}}) {short}\b", rf"\1 {full}", text)
    text = text.replace("-EQ", "")
    return " ".join(text.split())


def _run(item):
    kind, payload = item
    try:
        if kind == "sound":
            _play(payload)
        elif kind == "say":
            _say(payload)
    except (OSError, subprocess.SubprocessError):
        pass  # A voice that fails must never take the trading loop with it.


def _play(outcome):
    sound = SOUNDS.get(SYSTEM, {}).get(outcome)
    if not sound:
        return
    # Through the same stoppable path as speech: a sound that cannot be
    # stopped is a sound the microphone can open over.
    if SYSTEM == "Darwin":
        _speak_process(["afplay", sound])
    elif SYSTEM == "Windows":
        _speak_process(["powershell", "-NoProfile", "-Command",
                        f"[System.Media.SystemSounds]::{sound}.Play(); "
                        f"Start-Sleep -Milliseconds 400"])


def _say(text):
    rate = str(config.get("speech_rate") or 190)
    if SYSTEM == "Darwin":
        cmd = ["say", "-r", rate] + (["-v", _voice] if _voice else []) + [text]
    elif SYSTEM == "Windows":
        safe = text.replace("'", "''")
        cmd = ["powershell", "-NoProfile", "-Command",
               "Add-Type -AssemblyName System.Speech; "
               "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
               f"$s.Speak('{safe}')"]
    else:
        tool = shutil.which("espeak") or shutil.which("spd-say")
        if not tool:
            return
        cmd = [tool, text]
    _speak_process(cmd)


def _spawn(cmd):
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _speak_process(cmd):
    """Run a speech or sound command so that interrupt() can cut it short.

    Starting it and opening the microphone both happen under the lock, so
    one can never slip in just after the other has checked.
    """
    global _current
    while True:
        with _lock:
            if not _mic_open.is_set():
                proc = _current = _spawn(cmd)
                break
        time.sleep(0.05)
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
    finally:
        with _lock:
            _current = None


def _loop():
    global _running
    while True:
        item = _queue.get()
        # Anything announced while the user is recording waits until they
        # finish, so it can never end up in their command.
        while _mic_open.is_set():
            time.sleep(0.05)
        with _lock:
            _running = True
            _idle.clear()
        try:
            _run(item)
        finally:
            with _lock:
                _running = False
                if _queue.empty():
                    _idle.set()
            _queue.task_done()


def _start():
    global _worker, _voice
    if _worker is None:
        _voice = _pick_voice()
        _worker = threading.Thread(target=_loop, daemon=True)
        _worker.start()


def say(text):
    """Queue something to be spoken. Returns immediately."""
    if not text or not enabled():
        return
    _start()
    _idle.clear()
    _queue.put(("say", for_speech(text)))


def sound(outcome):
    """Queue the sound for an order outcome."""
    if not outcome or not enabled():
        return
    _start()
    _idle.clear()
    _queue.put(("sound", outcome))


def announce(result):
    """Speak an agent result, with its sound first.

    Every result that matters gets a sound: what happened to an order, a
    question back, or a refusal - so "nothing was sent" can be heard
    without listening to why.
    """
    if result.get("outcome"):
        sound(result["outcome"])
    elif result.get("needs_answer") or result.get("needs_clarification"):
        sound("question")
    elif result.get("blocked"):
        sound("blocked")
    say(result.get("speak"))


def interrupt():
    """Stop talking now and drop anything queued.

    Called when the user answers the confirmation box: once they have
    pressed a key, the rest of the preview is noise in front of the result.
    """
    while True:
        try:
            _queue.get_nowait()
            _queue.task_done()
        except queue.Empty:
            break
    with _lock:
        _stop_current()
        # Quiet only once the worker has actually finished what it was
        # playing - it reports that itself. Saying so here, early, is what
        # once let the microphone open over a sound.
        if not _running and _queue.empty():
            _idle.set()


def _stop_current():
    """Stop whatever is playing. Call with the lock held."""
    if _current is not None:
        try:
            _current.terminate()
        except OSError:
            pass


def wait_until_quiet(timeout=None):
    """Block until nothing is being spoken. True if it went quiet."""
    if _worker is None:
        return True
    return _idle.wait(timeout)


class listening:
    """Context manager: push to talk.

    Stops any speech or sound, then opens the microphone, and holds new
    announcements until it closes. Pressing to talk means "I'm talking
    now": waiting for a long readback to finish left people talking to a
    closed mic, and the old 30-second give-up opened it mid-sentence.
    """

    def __enter__(self):
        interrupt()
        # Only what was playing is left, and it was just stopped; the
        # bound is for a process that ignores being stopped.
        wait_until_quiet(2)
        with _lock:
            _mic_open.set()
            _stop_current()
        return self

    def __exit__(self, *exc):
        _mic_open.clear()


MEANINGS = [("filled", "Filled."),
            ("resting", "Placed, waiting to fill."),
            ("rejected", "Rejected."),
            ("question", "Anything else. Listen to what I say next.")]


def tour():
    """Play every sound with its meaning - learn them before trading."""
    if not enabled():
        print("Speech is off or unavailable on this machine.")
        return
    for outcome, meaning in MEANINGS:
        print(f"  {outcome:9} {meaning}")
        sound(outcome)
        say(meaning)
        wait_until_quiet(15)
        time.sleep(0.4)


if __name__ == "__main__":
    tour()
