"""Spoken readback and outcome sounds, so eyes can stay on the chart.

Two guarantees shape this module:

- It never talks over the microphone. Recording waits until speech has
  finished, and anything announced while recording waits until it stops.
  Otherwise the app's own readback - "buy one lot of the Nifty call" - could
  be heard as a command.
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

# Outcome -> sound. Distinct enough to tell apart without listening to words.
SOUNDS = {
    "Darwin": {
        "filled": "/System/Library/Sounds/Glass.aiff",
        "partial": "/System/Library/Sounds/Tink.aiff",
        "resting": "/System/Library/Sounds/Tink.aiff",
        "rejected": "/System/Library/Sounds/Basso.aiff",
        "unknown": "/System/Library/Sounds/Sosumi.aiff",
        "question": "/System/Library/Sounds/Pop.aiff",
    },
    "Windows": {
        "filled": "Asterisk", "partial": "Exclamation", "resting": "Exclamation",
        "rejected": "Hand", "unknown": "Hand", "question": "Question",
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
_current = None           # the speech process playing now, if any
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
    for line in out.splitlines():
        if "en_IN" in line:
            return line.split("  ")[0].strip()
    return None


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
    if SYSTEM == "Darwin":
        subprocess.run(["afplay", sound], timeout=5)
    elif SYSTEM == "Windows":
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"[System.Media.SystemSounds]::{sound}.Play(); "
                        f"Start-Sleep -Milliseconds 400"], timeout=5)


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


def _speak_process(cmd):
    """Run a speech command so that interrupt() can cut it short."""
    global _current
    with _lock:
        _current = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
    try:
        _current.wait(timeout=60)
    except subprocess.TimeoutExpired:
        _current.kill()
    finally:
        with _lock:
            _current = None


def _loop():
    while True:
        item = _queue.get()
        # Anything announced while the user is recording waits until they
        # finish, so it can never end up in their command.
        while _mic_open.is_set():
            time.sleep(0.05)
        _idle.clear()
        _run(item)
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
    """Speak an agent result, with its outcome sound first."""
    if result.get("outcome"):
        sound(result["outcome"])
    elif result.get("needs_answer"):
        sound("question")
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
        if _current is not None:
            try:
                _current.terminate()
            except OSError:
                pass
    if _queue.empty():
        _idle.set()


def wait_until_quiet(timeout=30):
    """Block until nothing is being spoken. Call before opening the mic."""
    if _worker is None:
        return
    _idle.wait(timeout)


class listening:
    """Context manager: hold announcements while the microphone is open."""

    def __enter__(self):
        wait_until_quiet()
        _mic_open.set()
        return self

    def __exit__(self, *exc):
        _mic_open.clear()
