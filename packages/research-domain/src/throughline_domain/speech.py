"""Turning recorded audio into timed words, on this machine.

This is the piece the voice subsystem has been waiting for, and where it runs
decides whether the product's first claim about itself survives. The browser's
own recogniser streams microphone audio to Google: one line of code, works well,
and a researcher thinking aloud about unpublished results or a participant's
details would be uploading all of it, with nothing in the interface to say so.

Whisper runs here instead. The API is already local — the whole product is — so
audio goes from the microphone to a process on the same machine and no further.

**Raw samples, not an audio file, and that removes a dependency rather than
adding one.** Whisper's usual path shells out to ffmpeg to decode whatever it was
given, and ffmpeg is not installed here and would be another thing a researcher
has to obtain before the feature works. The browser already has a complete audio
decoder, so it decodes and resamples, and this receives 16 kHz mono float32 —
which is exactly what the model consumes. Fewer moving parts, one less install,
and the format is fixed rather than inferred.

**Word timestamps are the point.** Speech and gesture are fused on a shared
clock, and a transcript with no per-word timing would date every word to the end
of the sentence — the exact mistake the typed path made, where "these" arrived
after the gesture it referred to. Whisper reports word times relative to the
clip, and the caller knows when the clip began, so the two compose into absolute
times on the same clock the hand frames use.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

#: The sample rate Whisper expects. The browser resamples to this before
#: sending, so nothing here has to guess or convert.
SAMPLE_RATE = 16_000

#: The smallest model, and the choice is deliberate rather than provisional.
#:
#: A researcher saying "why are these different" needs the *deictic* words right
#: and can tolerate a wrong technical term, because the sentence is a proposal
#: they confirm rather than a command that executes. Larger models are slower on
#: a laptop CPU, and latency here is not a comfort question: a transcript that
#: arrives four seconds late has missed the gesture window it was meant to bind
#: to, so accuracy bought with delay makes the feature *worse*.
DEFAULT_MODEL = "tiny.en"

#: Longer than this is refused rather than transcribed.
#:
#: A minute of audio on a laptop CPU takes long enough that the words could not
#: bind to anything, and holding a request open that long looks like a hang. The
#: interaction this exists for is a sentence, not a dictation.
MAX_SECONDS = 30.0

#: Raw request-body ceiling for 16 kHz mono float32 audio. The API enforces
#: this while streaming the request, before it can allocate an arbitrarily
#: large body and only then discover the clip is too long.
BYTES_PER_SAMPLE = 4
MAX_RAW_BYTES = int(SAMPLE_RATE * MAX_SECONDS * BYTES_PER_SAMPLE)


class SpeechError(Exception):
    """Audio this system will not transcribe, with a reason for a person."""


_model_lock = threading.Lock()
_model: Any = None
_model_name: str | None = None


def load_model(name: str = DEFAULT_MODEL) -> Any:
    """The model, loaded once and kept.

    Guarded by a lock because two researchers — or two clicks — arriving together
    would otherwise each pay the load, and the second would wait behind the first
    for no reason. Loading is seconds, not milliseconds.
    """
    global _model, _model_name
    with _model_lock:
        if _model is not None and _model_name == name:
            return _model
        import whisper  # imported lazily: nothing pays for it until used

        _model = whisper.load_model(name)
        _model_name = name
        return _model


def _samples_from(raw: bytes) -> np.ndarray:
    if len(raw) == 0:
        raise SpeechError("There was no audio in that recording.")
    if len(raw) % 4 != 0:
        raise SpeechError(
            "That audio is not 32-bit float samples. The recorder sends raw "
            "mono float32 at 16 kHz.")
    samples = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    if samples.size == 0:
        raise SpeechError("There was no audio in that recording.")
    if not np.isfinite(samples).all():
        # A NaN reaches the model as silence at best and an exception at worst,
        # and it means the recorder produced something it should not have.
        raise SpeechError("That audio contains values that are not numbers.")
    seconds = samples.size / SAMPLE_RATE
    if seconds > MAX_SECONDS:
        raise SpeechError(
            f"That recording is {seconds:.0f} seconds. This transcribes a "
            f"sentence, not a dictation — {MAX_SECONDS:.0f} seconds at most.")
    return samples


def transcribe(raw: bytes, *, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """Words and when each was said, relative to the start of the recording.

    Times are seconds from the beginning of the clip, never absolute: this
    process has no idea what the browser's monotonic clock reads, and inventing
    an absolute time here would put speech and gesture on different clocks —
    which this codebase has already shipped once, silently, and will not again.
    The caller knows when it started recording and does the addition.
    """
    samples = _samples_from(raw)

    result = load_model(model).transcribe(
        samples,
        word_timestamps=True,
        # No prompt and no temperature fallback: a research tool that guesses
        # harder when it is unsure produces confident wrong words, and a wrong
        # word here becomes a reference to the wrong cluster.
        condition_on_previous_text=False,
        fp16=False,
    )

    words: list[dict[str, Any]] = []
    for segment in result.get("segments") or []:
        for word in segment.get("words") or []:
            text = str(word.get("word", "")).strip()
            if not text:
                continue
            words.append({
                "text": text,
                "at": float(word.get("start", 0.0)),
                "until": float(word.get("end", 0.0)),
                # Whisper's own confidence, carried rather than dropped: a
                # deictic word recognised badly is the difference between the
                # right cluster and a confident wrong one.
                "confidence": float(word.get("probability", 0.0)),
            })

    return {
        "text": str(result.get("text", "")).strip(),
        "words": words,
        "seconds": samples.size / SAMPLE_RATE,
        "model": model,
        # Stated in the payload rather than only in the documentation, so
        # anything that stores or forwards a transcript carries the fact with it.
        "processed": "locally",
    }
