"""Phase 24 — Voice service: speech-to-text + text-to-speech.

A thin I/O shell around the existing Athena agent. The browser captures mic
audio (push-to-talk), POSTs it here for transcription, the frontend sends the
text to the agent's ``/chat``, then POSTs the reply back here to be spoken.
The agent's brain is untouched — no new LLM scope, no new secret.

Engines (both self-hosted, CPU-only — this runs on xdev-sr alongside the agent):

* **STT** — ``faster-whisper`` ``base.en`` at int8. Decodes WebM/Opus straight
  from the browser via PyAV (bundled with faster-whisper), so no ffmpeg shell-out.
* **TTS** — **Piper** with a British male voice (``en_GB-alan-medium``) — the
  "Jarvis-grade butler" choice. Fast on CPU, low latency. The ``.onnx`` voice
  model is baked into the image (see Dockerfile) so the first deploy is testable
  with no runtime download.

Both models load ONCE in the FastAPI lifespan; requests reuse the warm models.
Instrumented with the shared Phase 22 ``metrics.py`` (``track_job`` times STT/TTS
latency; ``job_empty_result`` fires the silent-failure counter on an empty
transcript / empty synthesis) so it shows up on the Athena Overview dashboard.
"""

import io
import logging
import os
import tempfile
import wave
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, UploadFile
from fastapi.responses import JSONResponse

import metrics

logger = logging.getLogger("voice")

# --- Config -----------------------------------------------------------------

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
PIPER_VOICE = os.getenv("PIPER_VOICE", "en_GB-alan-medium")
VOICES_DIR = os.getenv("PIPER_VOICES_DIR", "/app/voices")

# Warm models, populated in the lifespan and reused per-request.
_state: dict = {"stt": None, "tts": None}


# --- Model loading ----------------------------------------------------------


def _load_stt():
    from faster_whisper import WhisperModel

    logger.info("loading STT model", extra={"field": WHISPER_MODEL})
    # CPU int8 keeps base.en responsive on xdev-sr (no GPU on the cluster).
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type=WHISPER_COMPUTE)


def _load_tts():
    from piper.voice import PiperVoice

    model_path = os.path.join(VOICES_DIR, f"{PIPER_VOICE}.onnx")
    config_path = f"{model_path}.json"
    logger.info("loading TTS voice", extra={"field": PIPER_VOICE})
    return PiperVoice.load(model_path, config_path=config_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    metrics.configure_logging()
    # Load both engines once on startup so the first request is already warm.
    _state["stt"] = _load_stt()
    _state["tts"] = _load_tts()
    logger.info("voice service ready")
    yield
    _state.clear()


app = FastAPI(title="Athena Voice", lifespan=lifespan)
metrics.instrument_fastapi(app)


# --- Routes -----------------------------------------------------------------


@app.get("/healthz")
async def healthz():
    ready = _state.get("stt") is not None and _state.get("tts") is not None
    return JSONResponse(
        {"status": "ok" if ready else "loading", "voice": PIPER_VOICE, "stt": WHISPER_MODEL},
        status_code=200 if ready else 503,
    )


@app.post("/stt")
async def stt(audio: UploadFile | None = None, request: Request = None):
    """Transcribe an uploaded audio clip → ``{"text": ...}``.

    Accepts either a multipart ``audio`` file (browser ``MediaRecorder`` WebM/Opus)
    or a raw audio body. faster-whisper decodes the container itself via PyAV.
    """
    model = _state.get("stt")
    if model is None:
        return JSONResponse({"error": "stt model not loaded"}, status_code=503)

    raw = await audio.read() if audio is not None else await request.body()
    if not raw:
        return JSONResponse({"error": "empty audio"}, status_code=400)

    # Write to a temp file so PyAV can probe the container format from disk.
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name

    try:
        with metrics.track_job("stt"):
            segments, _info = model.transcribe(tmp_path, beam_size=1, vad_filter=True)
            text = "".join(seg.text for seg in segments).strip()
        if not text:
            # Silence / unintelligible — surface the silent-failure class loudly.
            metrics.job_empty_result("stt")
            logger.warning("empty transcript", extra={"job": "stt", "field": "text"})
        return JSONResponse({"text": text})
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.post("/tts")
async def tts(payload: dict):
    """Synthesize speech for ``{"text": ...}`` → ``audio/wav`` bytes (Piper)."""
    voice = _state.get("tts")
    if voice is None:
        return JSONResponse({"error": "tts voice not loaded"}, status_code=503)

    text = (payload or {}).get("text", "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)

    buf = io.BytesIO()
    with metrics.track_job("tts"):
        # Piper writes a complete WAV (params + frames) into the wave writer.
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize(text, wav_file)

    data = buf.getvalue()
    if len(data) <= 44:  # WAV header only, no audio frames
        metrics.job_empty_result("tts")
        logger.warning("empty synthesis", extra={"job": "tts", "field": "audio"})
    return Response(content=data, media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
