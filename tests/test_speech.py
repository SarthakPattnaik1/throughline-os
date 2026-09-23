"""Local transcription, and what it refuses (§38, §199).

The voice subsystem has been complete downstream of recognition for a while and
had no recogniser, because the browser's own one streams microphone audio to
Google and this product's first claim about itself is that nothing leaves the
machine. Whisper runs here instead — the API is already local, so audio goes from
the microphone to a process on the same machine and no further.

These tests are mostly about the refusals. Transcription accuracy is Whisper's
business and not something this codebase can meaningfully assert; what it owns is
the boundary: what it accepts, what it declines, and whether a time it reports
can be trusted to line up with a gesture.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from throughline_domain import speech

#: Transcription needs the optional `speech` extra (openai-whisper, and torch
#: behind it). The refusals below deliberately do not: they are checked before
#: the model is touched, so they still run on a clone that never installed it.
whisper_installed = importlib.util.find_spec("whisper") is not None
needs_whisper = pytest.mark.skipif(
    not whisper_installed,
    reason="openai-whisper is not installed (pip install -e '.[speech]')")


def silence(seconds: float) -> bytes:
    return np.zeros(int(speech.SAMPLE_RATE * seconds), dtype="<f4").tobytes()


class TestWhatItRefuses:
    def test_empty_audio(self):
        with pytest.raises(speech.SpeechError, match="no audio"):
            speech.transcribe(b"")

    def test_audio_that_is_not_float32(self):
        """The format is fixed rather than sniffed.

        The browser decodes and resamples, so this receives exactly one shape.
        Guessing at a container here is how ffmpeg ends up being required.
        """
        with pytest.raises(speech.SpeechError, match="32-bit float"):
            speech.transcribe(b"abc")

    def test_a_recording_longer_than_a_sentence(self):
        """A minute of audio on a laptop CPU could not bind to anything.

        The interaction this exists for is a sentence said while gesturing, and
        holding a request open long enough to transcribe a dictation looks like a
        hang rather than a feature.
        """
        with pytest.raises(speech.SpeechError, match="seconds"):
            speech.transcribe(silence(speech.MAX_SECONDS + 10))

    def test_values_that_are_not_numbers(self):
        # A NaN reaches the model as silence at best and an exception at worst,
        # and means the recorder produced something it should not have.
        bad = np.full(speech.SAMPLE_RATE, np.nan, dtype="<f4").tobytes()
        with pytest.raises(speech.SpeechError, match="not numbers"):
            speech.transcribe(bad)

    def test_a_refusal_costs_nothing(self):
        """Checked before the model is touched.

        Loading Whisper takes seconds. A malformed request that paid for a model
        load would be a way to make the API unresponsive with no audio at all.
        """
        speech._model = None  # noqa: SLF001 - asserting the guard order
        with pytest.raises(speech.SpeechError):
            speech.transcribe(b"")
        assert speech._model is None  # noqa: SLF001


@needs_whisper
class TestWhatItReturns:
    """One real run. Slow the first time — the model is 72MB — then cached."""

    def test_times_are_relative_to_the_clip(self):
        """Never absolute, and this is the property the fusion depends on.

        This process has no idea what the browser's monotonic clock reads.
        Inventing an absolute time would put speech and gesture on different
        clocks, which shipped once, silently, and made every reference resolve to
        nothing while nothing looked wrong.
        """
        result = speech.transcribe(silence(1.0))

        assert result["seconds"] == pytest.approx(1.0)
        for word in result["words"]:
            assert 0.0 <= word["at"] <= result["seconds"] + 1.0

    def test_it_says_where_it_ran(self):
        # Carried in the payload rather than only in the documentation, so
        # anything that stores or forwards a transcript carries the fact.
        assert speech.transcribe(silence(0.5))["processed"] == "locally"

    def test_every_word_carries_its_own_confidence(self):
        """A deictic word recognised badly is the difference between the right
        cluster and a confident wrong one, so the confidence is kept rather than
        dropped on the way out."""
        result = speech.transcribe(silence(0.5))
        for word in result["words"]:
            assert "confidence" in word
            assert 0.0 <= word["confidence"] <= 1.0

    def test_the_model_is_loaded_once(self):
        speech.transcribe(silence(0.3))
        first = speech._model  # noqa: SLF001
        speech.transcribe(silence(0.3))
        assert speech._model is first  # noqa: SLF001


class TestTheChoicesItMakes:
    def test_the_smallest_model_is_the_default(self):
        """Deliberate rather than provisional.

        A sentence is a proposal the researcher confirms, so a wrong technical
        term is recoverable — but latency is not a comfort question here. A
        transcript that arrives four seconds late has missed the gesture window
        it was meant to bind to, so accuracy bought with delay makes the feature
        worse.
        """
        assert speech.DEFAULT_MODEL == "tiny.en"

    def test_it_expects_the_rate_whisper_wants(self):
        # The browser resamples before sending, so nothing here converts or
        # guesses.
        assert speech.SAMPLE_RATE == 16_000



def test_speech_route_rejects_oversized_body_before_transcription(monkeypatch):
    from fastapi.testclient import TestClient

    from throughline_api.app import app
    from throughline_domain import auth
    from throughline_domain.db import transaction
    from throughline_domain.ids import new_id

    email = f"{new_id('usr')}@speech-limit.invalid"
    with transaction() as cur:
        user = auth.create_user(
            cur,
            email=email,
            display_name="Speech Limit",
            password="correct-horse-battery",
            is_admin=False,
        )
        token = auth.create_session(cur, user_id=user["id"])

    called = False

    def must_not_run(_raw):
        nonlocal called
        called = True
        raise AssertionError("oversized audio reached transcription")

    monkeypatch.setattr(speech, "transcribe", must_not_run)

    try:
        with TestClient(app) as client:
            client.cookies.set(auth.SESSION_COOKIE, token)
            response = client.post(
                "/api/speech/transcribe",
                content=b"\\x00" * (speech.MAX_RAW_BYTES + 1),
                headers={"Content-Type": "application/octet-stream"},
            )
        assert response.status_code == 413, response.text
        assert called is False
    finally:
        with transaction() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user["id"],))
