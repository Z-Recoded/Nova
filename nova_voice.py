# nova_voice.py
# Minimal-tier local voice input/output for Nova (Phase 2, ClickUp 86baeyg3q,
# scoped down per Marvin's 2026-07-19 call: local wake word + local STT +
# local TTS, desk-only -- explicitly not the full ticket's iPhone/Watch
# quick-capture apps or its 2-4s round-trip latency target.
#
# State machine:
#   IDLE (listening for wake word)
#     -> wake word detected -> RECORDING (until silence or max duration)
#     -> RECORDING done -> TRANSCRIBING (STT) -> nova_query.ask() -> SPEAKING (TTS)
#     -> SPEAKING done -> back to IDLE
#
# Reuses nova_query.ask() and nova_memory_store's load_history()/save_history()
# exactly the way nova_chat.py does -- voice and text chat share the same
# conversation history and the same RAG pipeline, no new query path.
#
# Wake word: "hey_jarvis" is a placeholder. openWakeWord ships only a
# handful of pretrained stock words (alexa, hey_mycroft, hey_jarvis,
# hey_rhasspy) -- a real "Hey Nova" model needs a separate synthetic-data
# training pass (~1 hour, openWakeWord's own training pipeline), deliberately
# NOT done here so the rest of the pipeline could be proven working first.
# Swap WAKE_WORD_MODEL_NAME once that training is done.
#
# Known real limitation, confirmed live during implementation: this venv's
# torch build is CPU-only (no CUDA), so STT runs on CPU -- a short clip took
# ~8s to transcribe in testing, well short of the ticket's original latency
# target. Reinstalling a CUDA-enabled torch build would fix this but is a
# large (~2-3GB), environment-wide change deliberately left for Marvin to
# decide on rather than done silently here.
#
# Run:
#   nova-env\\Scripts\\python nova_voice.py

import os
import subprocess
import sys
import time

import numpy as np
import sounddevice as sd
import torch
from openwakeword.model import Model as WakeWordModel
from openwakeword.utils import download_models as download_wakeword_models
from piper import PiperVoice
from transformers import pipeline

from nova_memory_store import load_history, save_history
from nova_query import ask

# ── Config ─────────────────────────────────────────────────────
SAMPLE_RATE = 16_000
FRAME_SIZE = 1_280  # 80ms at 16kHz -- openWakeWord's expected chunk size

WAKE_WORD_MODEL_NAME = "hey_jarvis"  # placeholder, see module docstring
WAKE_WORD_SCORE_THRESHOLD = 0.5

# int16 RMS threshold for "someone is talking" -- tune empirically per mic/room.
SILENCE_RMS_THRESHOLD = 300
SILENCE_TIMEOUT_SECONDS = 1.5  # stop recording after this much continuous silence

# Real bug found live 2026-07-19: 0.5s let brief noise (a stray sound, mic bleed from the
# wake word's own tail) through the had_speech gate, which Whisper then transcribed into a
# short, plausible-but-wrong phrase ("Thank you.", "Who is now?"). A word-count filter on
# the transcript was considered and rejected -- it would also reject genuine short queries
# that are core to how Nova is actually used ("Who is Null?", "What is KAS?" are the same
# length as the garbled ones). Duration is a better signal: a real spoken question takes
# roughly a second either way, brief noise usually doesn't.
MIN_SPEECH_SECONDS = 1.0

MAX_RECORDING_SECONDS = 15  # safety cap regardless of silence detection

# Backstop for Whisper's own well-documented hallucination artifacts (sign-off phrases from
# its training data) that could still slip past the duration gate. Checked against the full
# transcript, case-insensitive, punctuation-stripped. Not exhaustive -- a garbled-but-
# question-shaped transcript (e.g. "Who is now?") won't match this list and relies on the
# duration gate above instead; this is a known, accepted remaining gap, not a full fix.
KNOWN_HALLUCINATION_PHRASES = {
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "thanks",
    "bye",
    "goodbye",
    "you",
    "the end",
}

VOICE_MODEL_DIR = "voice_models"
VOICE_MODEL_NAME = "en_US-lessac-medium"
VOICE_MODEL_PATH = os.path.join(VOICE_MODEL_DIR, f"{VOICE_MODEL_NAME}.onnx")

STT_MODEL_NAME = "distil-whisper/distil-large-v3"


# ── Setup ──────────────────────────────────────────────────────

def _ensure_models_downloaded() -> None:
    """Download the wake-word and TTS voice models on first run, if missing."""
    download_wakeword_models()  # no-op if already cached; openWakeWord's own idempotent fetch

    if not os.path.exists(VOICE_MODEL_PATH):
        print(f"[nova_voice] downloading Piper voice '{VOICE_MODEL_NAME}'...")
        os.makedirs(VOICE_MODEL_DIR, exist_ok=True)
        subprocess.run(
            [sys.executable, "-m", "piper.download_voices", VOICE_MODEL_NAME,
             "--download-dir", VOICE_MODEL_DIR],
            check=True,
        )


def _load_stt_pipeline():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[nova_voice] loading STT model on {device}...")
    return pipeline(
        "automatic-speech-recognition",
        model=STT_MODEL_NAME,
        dtype=torch.float16 if device == "cuda" else torch.float32,
        device=device,
    )


# ── Audio helpers ──────────────────────────────────────────────

def _rms(chunk: np.ndarray) -> float:
    """Root-mean-square amplitude of an int16 audio chunk -- simple speech/silence signal."""
    return float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))


def _record_until_silence(stream: sd.InputStream) -> np.ndarray:
    """
    Accumulate audio chunks from an already-open stream until the speaker
    goes quiet for SILENCE_TIMEOUT_SECONDS, or MAX_RECORDING_SECONDS is hit.
    No separate VAD library -- a simple RMS-energy threshold is enough for
    this scope (desk-quiet room, deliberate short voice commands).

    Returns (audio, had_speech) -- had_speech is False if the recording
    never accumulated MIN_SPEECH_SECONDS of above-threshold audio (e.g. the
    wake word fired but nothing else was said). Real bug found live
    2026-07-19: feeding a mostly-silent recording straight to Whisper made
    it HALLUCINATE a plausible-sounding phrase ("Thank you.") instead of
    returning empty -- a known Whisper-family artifact from its training
    data (sign-off phrases from transcribed videos). Callers must check
    had_speech and skip transcription entirely rather than trust Whisper to
    self-report silence.
    """
    frames = []
    silent_chunks = 0
    speech_chunks = 0
    silence_chunk_limit = int(SILENCE_TIMEOUT_SECONDS * SAMPLE_RATE / FRAME_SIZE)
    min_speech_chunks = int(MIN_SPEECH_SECONDS * SAMPLE_RATE / FRAME_SIZE)
    max_chunks = int(MAX_RECORDING_SECONDS * SAMPLE_RATE / FRAME_SIZE)

    for _ in range(max_chunks):
        chunk, _ = stream.read(FRAME_SIZE)
        chunk = chunk[:, 0]  # mono
        frames.append(chunk)

        if _rms(chunk) < SILENCE_RMS_THRESHOLD:
            silent_chunks += 1
        else:
            silent_chunks = 0
            speech_chunks += 1

        if speech_chunks >= min_speech_chunks and silent_chunks >= silence_chunk_limit:
            break

    audio = np.concatenate(frames) if frames else np.array([], dtype=np.int16)
    had_speech = speech_chunks >= min_speech_chunks
    return audio, had_speech


def transcribe(asr_pipeline, audio: np.ndarray) -> str:
    """Run the STT pipeline on a raw int16 audio array, bypassing ffmpeg entirely
    (confirmed live: the pipeline's default file-path loader needs ffmpeg, which
    isn't installed here -- passing a raw array + sample rate sidesteps it)."""
    normalized = audio.astype(np.float32) / 32768.0
    result = asr_pipeline({"raw": normalized, "sampling_rate": SAMPLE_RATE})
    return result["text"].strip()


def speak(voice: PiperVoice, text: str) -> None:
    """Synthesize text to audio and play it back through the default output device."""
    audio_chunks = []
    sample_rate = None
    for audio_chunk in voice.synthesize(text):
        # AudioChunk objects expose raw int16 PCM bytes + the voice's sample rate.
        sample_rate = audio_chunk.sample_rate
        audio_chunks.append(np.frombuffer(audio_chunk.audio_int16_bytes, dtype=np.int16))
    if not audio_chunks:
        return
    full_audio = np.concatenate(audio_chunks)
    sd.play(full_audio, samplerate=sample_rate)
    sd.wait()


# ── Main loop ──────────────────────────────────────────────────

def run_voice_loop() -> None:
    _ensure_models_downloaded()

    wake_word_model = WakeWordModel(wakeword_models=[WAKE_WORD_MODEL_NAME], inference_framework="onnx")
    asr_pipeline = _load_stt_pipeline()
    tts_voice = PiperVoice.load(VOICE_MODEL_PATH)

    history = load_history()
    if history:
        print(f"  Resumed {len(history) // 2} exchange(s) from last session.\n")

    print(f"[nova_voice] listening for wake word '{WAKE_WORD_MODEL_NAME}'... (Ctrl+C to quit)")

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SIZE) as stream:
        try:
            while True:
                chunk, _ = stream.read(FRAME_SIZE)
                scores = wake_word_model.predict(chunk[:, 0])

                if scores[WAKE_WORD_MODEL_NAME] < WAKE_WORD_SCORE_THRESHOLD:
                    continue

                print("\n[nova_voice] wake word detected, listening...")
                wake_word_model.reset()  # clear internal buffers so the same trigger doesn't immediately re-fire
                audio, had_speech = _record_until_silence(stream)

                if not had_speech:
                    # Real bug found live 2026-07-19: skip STT entirely on a mostly-silent
                    # recording -- Whisper hallucinates a plausible phrase ("Thank you.")
                    # instead of returning empty, and that hallucinated text then drove a
                    # real (bogus) RAG answer. Never let silence reach the STT model.
                    print("[nova_voice] didn't hear anything, back to listening.\n")
                    continue

                print("[nova_voice] transcribing...")
                transcript = transcribe(asr_pipeline, audio)
                normalized_transcript = transcript.strip().lower().rstrip(".!?")
                if not transcript or normalized_transcript in KNOWN_HALLUCINATION_PHRASES:
                    # Backstop for Whisper's own hallucination artifacts that made it past
                    # the had_speech duration gate -- see KNOWN_HALLUCINATION_PHRASES' comment.
                    print("[nova_voice] didn't catch that clearly, back to listening.\n")
                    continue
                print(f"You (voice): {transcript}")

                result = ask(transcript, history=history, persist=True)
                answer = result["answer"]
                print(f"Nova: {answer}\n")

                history.append({"role": "user", "content": transcript})
                history.append({"role": "assistant", "content": answer})

                speak(tts_voice, answer)
                print(f"[nova_voice] listening for wake word '{WAKE_WORD_MODEL_NAME}'...")

        except KeyboardInterrupt:
            print("\nNova voice offline.")
            save_history(history)


if __name__ == "__main__":
    run_voice_loop()
