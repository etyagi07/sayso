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


def test_three_sounds_to_learn_and_one_for_everything_else():
    # Filled, waiting and rejected are told apart by ear; everything else
    # shares one "listen to the words" sound. More than that is too many
    # to keep straight mid-trade.
    for system, table in speak.SOUNDS.items():
        key = {table[o] for o in ("filled", "resting", "rejected")}
        rest = {table[o] for o in ("partial", "unknown", "question",
                                   "blocked")}
        assert len(key) == 3, (system, table)
        assert len(rest) == 1 and not rest & key, (system, table)
    # The sound tour plays each distinct sound exactly once.
    mac = speak.SOUNDS["Darwin"]
    toured = [mac[o] for o, _ in speak.MEANINGS]
    assert sorted(toured) == sorted(set(mac.values())), speak.MEANINGS


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


@with_fake_audio(delay=5.0)
def test_a_fill_is_not_thrown_away_by_a_keypress(played):
    # A background fill queued while a new order's preview was being read
    # was dropped when the user pressed y - they thought it still rested.
    speak.say("buy one lot of the Nifty call, about 5,000 rupees")
    time.sleep(0.1)
    speak.sound("filled", keep=True)
    speak.say("Nifty 23100 call filled at 80.", keep=True)
    speak.interrupt()                       # the user pressed y
    assert played[0].killed, "preview kept talking"
    for p in list(played):
        p.stopped.set()                     # let each 'finish' at once
    deadline = time.monotonic() + 3
    while len(played) < 3 and time.monotonic() < deadline:
        for p in list(played):
            p.stopped.set()
        time.sleep(0.02)
    assert [p.cmd[-1] for p in played[1:3]] == [
        speak.SOUNDS["Darwin"]["filled"], "Nifty 23100 call filled at 80."], \
        [p.cmd for p in played]


@with_fake_audio(delay=5.0)
def test_a_fill_cut_off_by_push_to_talk_is_replayed(played):
    speak.say("Nifty 23100 call filled at 80.", keep=True)
    time.sleep(0.1)
    with speak.listening():
        assert played[0].stopped.is_set(), "still speaking into the mic"
        time.sleep(0.1)
        assert len(played) == 1, "played while the mic was open"
    deadline = time.monotonic() + 3
    while len(played) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(played) == 2 and played[1].cmd == played[0].cmd, \
        [p.cmd for p in played]
    assert not played[1].mic_open_at_start


@with_fake_audio(delay=5.0)
def test_stale_chatter_is_not_read_after_you_speak(played):
    speak.say("first")                      # playing
    time.sleep(0.1)
    speak.say("I didn't follow that.")      # queued, not news
    with speak.listening():
        pass
    time.sleep(0.3)
    assert all("didn't follow" not in p.cmd[-1] for p in played), \
        [p.cmd for p in played]


def test_windows_commands_are_built_safely():
    # Built, not run: there is no Windows here. Curly apostrophes ended the
    # PowerShell string, so the phrase was never heard; sounds now come
    # from distinct files, with the old system sound if a file is missing.
    import os
    import tempfile
    built = []
    saved = (speak.SYSTEM, speak._speak_process, os.environ.get("WINDIR"))
    speak.SYSTEM = "Windows"
    speak._speak_process = built.append
    media = Path(tempfile.mkdtemp())
    (media / "Media").mkdir()
    (media / "Media" / "tada.wav").write_bytes(b"")
    os.environ["WINDIR"] = str(media)
    try:
        speak._say("Don\u2019t buy Reliance\u2019s call")
        speak._play("filled")
        speak._play("rejected")               # its file is missing here
    finally:
        speak.SYSTEM, speak._speak_process = saved[:2]
        if saved[2] is None:
            os.environ.pop("WINDIR", None)
        else:
            os.environ["WINDIR"] = saved[2]
    said, filled, rejected = (c[-1] for c in built)
    assert "Don''t buy Reliance''s" in said and "\u2019" not in said, said
    assert "SoundPlayer" in filled and "tada.wav" in filled, filled
    assert "SystemSounds]::Hand" in rejected, rejected


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
