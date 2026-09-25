"""Per-machine settings.

Anything that depends on the computer rather than the code lives here -
microphone sensitivity above all. A threshold tuned on one laptop is
wrong on the next one, and the failure is silent: either it never hears
you, or it never stops listening.

Stored beside the project so a fresh clone starts unconfigured and says
so, rather than inheriting somebody else's microphone.
"""

import json
import platform
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

DEFAULTS = {
    # Set by `python -m voice.calibrate`. None means "not yet measured".
    "silence_rms": None,
    "silence_seconds": 1.4,
    "max_seconds": 15,
    "min_speech_seconds": 0.4,
    # "auto" picks MLX on Apple Silicon and faster-whisper elsewhere.
    "asr_backend": "auto",
    "asr_model": "small.en",
    "input_device": None,      # None = the system default
}


def is_apple_silicon():
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def load():
    settings = dict(DEFAULTS)
    try:
        settings.update(json.loads(CONFIG_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return settings


def save(**changes):
    settings = load()
    settings.update(changes)
    CONFIG_FILE.write_text(json.dumps(settings, indent=2) + "\n")
    return settings


def get(key):
    return load().get(key, DEFAULTS.get(key))


def is_calibrated():
    return load().get("silence_rms") is not None


def backend():
    """Which speech recogniser to use on this machine."""
    choice = get("asr_backend")
    if choice != "auto":
        return choice
    return "mlx" if is_apple_silicon() else "faster-whisper"
