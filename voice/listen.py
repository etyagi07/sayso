"""Local speech capture and transcription via Whisper (MLX, on-device).

Activation is a toggle, not push-to-hold: press Enter, the indicator turns
red, speak, and it stops on its own once you go quiet. Nothing is recorded
between turns - the stream only opens while armed.
"""

import sys
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000          # what Whisper expects
# Measured on this Mac at input volume 85: the room floor sits around 0.013
# (fan/ambient) and actual speech peaks 0.06-0.19. Sit between the two, well
# clear of the floor, or the turn never ends.
SILENCE_RMS = 0.030          # below this counts as quiet
SILENCE_SECONDS = 1.4        # quiet for this long ends the turn
MAX_SECONDS = 15             # hard stop, so a stuck mic cannot run forever
MIN_SPEECH_SECONDS = 0.4     # ignore a stray keypress or cough

# Small model: fast on Apple Silicon, accurate enough for short commands.
MODEL = "mlx-community/whisper-small.en-mlx"

R, G, DIM, X = "\033[91m", "\033[92m", "\033[2m", "\033[0m"

_model_ready = False


def warm_up():
    """Load the model once, so the first command isn't slow."""
    global _model_ready
    if _model_ready:
        return
    import mlx_whisper
    print(f"{DIM}  loading whisper ({MODEL.split('/')[-1]})...{X}", end="", flush=True)
    mlx_whisper.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32),
                           path_or_hf_repo=MODEL)
    _model_ready = True
    print(f"\r{DIM}  whisper ready{' ' * 30}{X}")


def record_until_silence():
    """Record from the mic until the speaker stops. Returns float32 audio."""
    chunks, silent_for, spoke_for = [], 0.0, 0.0
    block = int(SAMPLE_RATE * 0.05)  # 50ms blocks

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="float32", blocksize=block) as stream:
        print(f"  {R}● RECORDING{X} {DIM}(speak, then pause){X}", end="", flush=True)
        while True:
            data, _ = stream.read(block)
            mono = data[:, 0]
            chunks.append(mono.copy())

            rms = float(np.sqrt(np.mean(mono ** 2)))
            seconds = block / SAMPLE_RATE
            if rms < SILENCE_RMS:
                silent_for += seconds
            else:
                silent_for = 0.0
                spoke_for += seconds

            elapsed = len(chunks) * seconds
            if spoke_for >= MIN_SPEECH_SECONDS and silent_for >= SILENCE_SECONDS:
                break
            if elapsed >= MAX_SECONDS:
                break

    print(f"\r  {DIM}○ processing...{' ' * 25}{X}", end="", flush=True)
    audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
    return audio if spoke_for >= MIN_SPEECH_SECONDS else None


def transcribe(audio):
    import mlx_whisper
    result = mlx_whisper.transcribe(
        audio, path_or_hf_repo=MODEL, language="en",
        # Bias the decoder toward the vocabulary it will actually hear.
        # Bias the decoder toward this vocabulary. Without it YESBANK comes
        # back as "years bank" or "yes bank".
        initial_prompt=(
            "Stock trading commands. Buy one YESBANK at twenty three "
            "point two two. Sell two YESBANK at twenty three point two "
            "zero. What is YESBANK at? "
            "Tickers: YESBANK, RELIANCE, NIFTYBEES, SBIN, INFY."
        ),
    )
    return (result.get("text") or "").strip()


def listen_once():
    """Arm, record, transcribe. Returns the transcript, or None."""
    audio = record_until_silence()
    if audio is None:
        print(f"\r  {DIM}○ nothing heard{' ' * 25}{X}")
        return None
    text = transcribe(audio)
    print(f"\r{' ' * 50}\r", end="")
    return text or None


if __name__ == "__main__":
    warm_up()
    print(f"\n{G}● READY{X} {DIM}press Enter to speak, ctrl-c to quit{X}")
    while True:
        try:
            input()
            said = listen_once()
            print(f"  heard: {said!r}" if said else f"  {DIM}(silence){X}")
        except KeyboardInterrupt:
            print(f"\n{DIM}○ stopped{X}")
            sys.exit(0)
