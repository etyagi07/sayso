"""Speech ordering and the microphone gate - without making any sound."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import speak  # noqa: E402


class Recorder:
    """Stands in for the audio backend and records what would have played."""

    def __init__(self, delay=0.0):
        self.played = []
        self.delay = delay

    def __call__(self, item):
        time.sleep(self.delay)
        self.played.append((item, time.monotonic(), speak._mic_open.is_set()))


def with_recorder(delay=0.0):
    def wrap(fn):
        def run():
            real_run, real_enabled = speak._run, speak.enabled
            rec = Recorder(delay)
            speak._run = rec
            speak.enabled = lambda: True
            try:
                fn(rec)
            finally:
                speak.wait_until_quiet(5)
                speak._run, speak.enabled = real_run, real_enabled
        run.__name__ = fn.__name__
        return run
    return wrap


def test_order_numbers_are_not_read_aloud():
    out = speak.for_speech("Placed. Order 26092300243718 is resting at 23.20.")
    assert "2609" not in out and "The order is resting" in out


def test_months_are_said_in_full():
    assert "1 October" in speak.for_speech("the Sensex 1 Oct 73900 call")


@with_recorder()
def test_outcome_sound_plays_before_the_words(rec):
    speak.announce({"speak": "Filled.", "outcome": "filled"})
    speak.wait_until_quiet(5)
    kinds = [item[0] for item, _, _ in rec.played]
    assert kinds == ["sound", "say"], kinds


@with_recorder()
def test_nothing_is_spoken_while_the_mic_is_open(rec):
    # The app must never hear its own readback as a command.
    with speak.listening():
        speak.say("buy one lot of the Nifty call")
        time.sleep(0.3)
        assert not rec.played, "spoke into an open microphone"
    speak.wait_until_quiet(5)
    assert rec.played and not rec.played[0][2], "played while mic open"


@with_recorder(delay=0.3)
def test_mic_waits_for_speech_to_finish(rec):
    speak.say("a sentence that takes a moment")
    time.sleep(0.05)
    started = time.monotonic()
    with speak.listening():
        opened = time.monotonic()
    finished = rec.played[-1][1]
    assert opened >= finished, "mic opened before the readback ended"
    assert opened - started >= 0.2


@with_recorder(delay=0.2)
def test_answering_cuts_the_preview_short(rec):
    # Pressing y mid-readback should get the result spoken next, not after
    # the rest of a preview the user has already acted on.
    for _ in range(5):
        speak.say("a long preview sentence")
    time.sleep(0.05)
    speak.interrupt()
    speak.wait_until_quiet(5)
    assert len(rec.played) <= 2, f"kept talking: {len(rec.played)} items"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            passed += 1
            print(f"ok   {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
