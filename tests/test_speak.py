"""Speech ordering, outcome sounds and the microphone gate - without making
any sound. The speech processes are faked; everything else is real."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import speak  # noqa: E402


class FakeProc:
    """A speech or sound process that 'plays' for `delay` seconds."""

    def __init__(self, cmd, delay, log):
        self.cmd, self.stopped = cmd, threading.Event()
        self.killed = False
        self.started = time.monotonic()
        self.mic_open_at_start = speak._mic_open.is_set()
        self.delay = delay
        log.append(self)

    def wait(self, timeout=None):
        if not self.stopped.wait(min(self.delay, timeout or self.delay)):
            if timeout is not None and timeout < self.delay:
                raise speak.subprocess.TimeoutExpired(self.cmd, timeout)
        self.ended = time.monotonic()
        return 0

    def terminate(self):
        self.killed = True
        self.stopped.set()

    kill = terminate


def with_fake_audio(delay=0.0):
    def wrap(fn):
        def run():
            played = []
            saved = (speak._spawn, speak.enabled, speak.SYSTEM, speak._voice)
            speak._spawn = lambda cmd: FakeProc(cmd, delay, played)
            speak.enabled = lambda: True
            speak.SYSTEM = "Darwin"
            try:
                fn(played)
            finally:
                speak.interrupt()
                speak.wait_until_quiet(5)
                (speak._spawn, speak.enabled, speak.SYSTEM,
                 speak._voice) = saved
        run.__name__ = fn.__name__
        return run
    return wrap


def kinds(played):
    return ["sound" if p.cmd[0] == "afplay" else "say" for p in played]


def test_order_numbers_are_not_read_aloud():
    out = speak.for_speech("Placed. Order 26092300243718 is resting at 23.20.")
    assert "2609" not in out and "The order is resting" in out


def test_months_are_said_in_full():
    assert "1 October" in speak.for_speech("the Sensex 1 Oct 73900 call")


def test_every_outcome_has_its_own_sound():
    # A sound is only useful if it can be told apart without the words.
    mac = speak.SOUNDS["Darwin"]
    for outcome in ("filled", "partial", "resting", "rejected", "unknown",
                    "question", "blocked"):
        assert outcome in mac, outcome
    assert len(set(mac.values())) == len(mac), mac
    # The sound tour teaches every one of them.
    assert {o for o, _ in speak.MEANINGS} == set(mac), speak.MEANINGS


@with_fake_audio()
def test_outcome_sound_plays_before_the_words(played):
    speak.announce({"speak": "Filled.", "outcome": "filled"})
    speak.wait_until_quiet(5)
    assert kinds(played) == ["sound", "say"], kinds(played)
    assert played[0].cmd[-1].endswith("Glass.aiff")


@with_fake_audio()
def test_a_refusal_has_a_sound_too(played):
    # "I heard both buy and sell" - nothing was sent. Eyes on the chart
    # need to know that without listening to the whole sentence.
    speak.announce({"speak": "I heard both buy and sell.", "blocked": True,
                    "needs_clarification": True})
    speak.announce({"speak": "Over the limit.", "blocked": True})
    speak.wait_until_quiet(5)
    sounds = [p.cmd[-1] for p in played if p.cmd[0] == "afplay"]
    assert sounds == [speak.SOUNDS["Darwin"]["question"],
                      speak.SOUNDS["Darwin"]["blocked"]], sounds


@with_fake_audio()
def test_nothing_is_spoken_while_the_mic_is_open(played):
    # The app must never hear its own readback as a command.
    with speak.listening():
        speak.say("buy one lot of the Nifty call")
        time.sleep(0.3)
        assert not played, "spoke into an open microphone"
    speak.wait_until_quiet(5)
    assert played and not played[0].mic_open_at_start


@with_fake_audio(delay=5.0)
def test_pressing_to_talk_stops_the_readback_first(played):
    # Enter means "I'm talking now". Waiting for a long readback left the
    # user talking to a closed mic; giving up after 30 seconds opened the
    # mic while it was still speaking.
    speak.say("a long readback of every position you hold")
    time.sleep(0.1)
    started = time.monotonic()
    with speak.listening():
        opened = time.monotonic()
        assert all(p.stopped.is_set() for p in played), "still speaking"
    assert opened - started < 1.0, f"mic took {opened - started:.1f}s"
    assert played[0].killed


@with_fake_audio(delay=0.2)
def test_answering_cuts_the_preview_short(played):
    # Pressing y mid-readback should get the result spoken next, not after
    # the rest of a preview the user has already acted on.
    for _ in range(5):
        speak.say("a long preview sentence")
    time.sleep(0.05)
    speak.interrupt()
    speak.wait_until_quiet(5)
    assert len(played) <= 2, f"kept talking: {len(played)} items"


@with_fake_audio(delay=5.0)
def test_pressing_to_talk_stops_a_sound_too(played):
    # Sounds used to play in a way nothing could stop, and "quiet" could be
    # reported while one was still playing - so the mic could open over it.
    speak.sound("filled")
    time.sleep(0.1)
    with speak.listening():
        assert played and played[0].stopped.is_set(), "sound still playing"


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
