"""Is the mic actually delivering audio? Records 5s and reports levels."""

import numpy as np
import sounddevice as sd

from voice.listen import SAMPLE_RATE, SILENCE_RMS

DUR = 5

print(f"input device: {sd.query_devices(kind='input')['name']}")
print(f"threshold   : {SILENCE_RMS}")
print(f"\nSpeak now for {DUR} seconds...\n")

block = int(SAMPLE_RATE * 0.25)
peaks = []
with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                    dtype="float32", blocksize=block) as stream:
    for i in range(int(DUR * 4)):
        data, _ = stream.read(block)
        rms = float(np.sqrt(np.mean(data[:, 0] ** 2)))
        peaks.append(rms)
        bar = "█" * min(40, int(rms * 400))
        flag = "SPEECH" if rms >= SILENCE_RMS else "quiet"
        print(f"  {rms:.4f} {flag:<7}|{bar}")

hi, avg = max(peaks), sum(peaks) / len(peaks)
print(f"\npeak {hi:.4f}   average {avg:.4f}   threshold {SILENCE_RMS}")
if hi < 0.001:
    print("\n=> The mic is returning SILENCE. macOS is most likely blocking it.")
    print("   System Settings > Privacy & Security > Microphone, enable the")
    print("   terminal app you are running this in, then restart that app.")
elif hi < SILENCE_RMS:
    print(f"\n=> Audio is arriving but too quiet. Lower SILENCE_RMS to about "
          f"{hi * 0.4:.4f} in voice/listen.py")
else:
    print("\n=> Mic is working and loud enough. Threshold is fine.")
