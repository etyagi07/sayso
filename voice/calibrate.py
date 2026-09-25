"""Measure this microphone, in this room, and set the speech threshold.

Run: python -m voice.calibrate

The threshold that decides "you stopped talking" depends entirely on the
hardware and the room. Set it too low and the fan keeps the recording
open forever; too high and it cuts you off mid-sentence. Measuring both
the silence floor and actual speech, then placing the threshold between
them, is the only reliable way to get it right on an unknown machine.
"""

import sys

import numpy as np
import sounddevice as sd

from voice import config

SAMPLE_RATE = 16000
BLOCK = int(SAMPLE_RATE * 0.05)

G, R, Y, DIM, X = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"


def _measure(seconds, label):
    levels = []
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="float32", blocksize=BLOCK,
                        device=config.get("input_device")) as stream:
        blocks = int(seconds / 0.05)
        for i in range(blocks):
            data, _ = stream.read(BLOCK)
            rms = float(np.sqrt(np.mean(data[:, 0] ** 2)))
            levels.append(rms)
            bar = "#" * min(40, int(rms * 400))
            done = int((i + 1) / blocks * 20)
            print(f"\r  {label} [{'=' * done}{' ' * (20 - done)}] "
                  f"{rms:.4f} {bar[:24]:<24}", end="", flush=True)
    print()
    return levels


def calibrate():
    try:
        device = sd.query_devices(kind="input")
    except Exception as e:
        print(f"{R}No microphone available: {e}{X}")
        return 1
    print(f"\nMicrophone: {device['name']}\n")

    print(f"{DIM}Step 1 of 2 - stay quiet for 3 seconds.{X}")
    input("  press Enter when ready: ")
    quiet = _measure(3, "silence")
    floor = float(np.percentile(quiet, 90))

    print(f"\n{DIM}Step 2 of 2 - say \"buy one call at market\" a few times.{X}")
    input("  press Enter when ready: ")
    loud = _measure(5, "speech ")
    # p90, not the mean: most of a "speak now" window is the gaps between
    # words, which sit at the noise floor and would drag the estimate down.
    speech = float(np.percentile(loud, 90))

    print(f"\n  room floor : {floor:.4f}")
    print(f"  your speech: {speech:.4f}")

    if speech < floor * 2:
        print(f"\n{R}Speech is barely above the background ({speech:.4f} vs "
              f"{floor:.4f}).{X}")
        print("  Either the microphone did not pick you up, or the room is "
              "noisy.")
        print("  Try: move closer, raise the input volume in system settings, "
              "then run this again.")
        return 1

    # Clear of the room but well under speech. 2.5x the floor is enough
    # that ambient noise never holds the turn open; capping at a third of
    # speech level keeps quieter words from ending it early. On the
    # machine this was developed on (floor 0.013, speech 0.19) this gives
    # 0.033, matching the value that was hand-tuned over several attempts.
    threshold = round(min(floor * 2.5, speech / 3), 4)
    config.save(silence_rms=threshold)

    print(f"\n{G}Calibrated.{X} threshold {threshold:.4f}")
    print(f"  {DIM}room is {threshold / floor:.1f}x below it, "
          f"speech {speech / threshold:.1f}x above{X}")
    print(f"  {DIM}saved to config.json{X}\n")
    return 0


if __name__ == "__main__":
    sys.exit(calibrate())
