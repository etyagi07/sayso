"""Microphone calibration, against a fake sound device. Offline, silent."""

import builtins
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import calibrate, config  # noqa: E402


class FakeMic:
    """A sound device whose level is `quiet` first, then `speech`."""

    def __init__(self, quiet, speech):
        self.levels = [quiet, speech]
        self.opened = 0

    def query_devices(self, kind=None):
        return {"name": "Fake microphone"}

    def InputStream(self, **k):
        self.opened += 1
        level = self.levels.pop(0) if len(self.levels) > 1 else self.levels[0]
        mic = self

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *e):
                mic.levels = mic.levels  # noqa

            def read(self, n):
                return np.full((n, 1), level, dtype="float32"), False
        return Stream()


def run(quiet, speech):
    saved = (calibrate.sd, builtins.input, config.save)
    stored = {}
    calibrate.sd = mic = FakeMic(quiet, speech)
    run.mic = mic
    builtins.input = lambda prompt="": ""
    config.save = lambda **k: stored.update(k)
    try:
        return calibrate.calibrate(), stored
    finally:
        calibrate.sd, builtins.input, config.save = saved


def test_a_silent_microphone_is_explained_not_saved():
    # Permission denied reads as all zeros. It was saved as threshold 0,
    # then crashed dividing by it - and every recording ran 15 seconds.
    code, stored = run(0.0, 0.0)
    assert code == 1 and not stored, stored
    # Stopped at the quiet step - not after asking to speak into a mic
    # that delivers nothing.
    assert run.mic.opened == 1, run.mic.opened


def test_a_working_microphone_is_calibrated():
    code, stored = run(0.005, 0.07)
    assert code == 0 and 0 < stored["silence_rms"] < 0.07, stored


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
