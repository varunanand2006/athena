# Phase 24 — Voice service (STT + TTS). Built on xdev-sr, runs on xdev-sr.
# NOTE: this Dockerfile does NOT read pyproject.toml — keep the pip list below
# in sync with pyproject.toml by hand (same trap as langchain-openai / metrics.py).
#
# Python 3.10 (NOT 3.12): piper-tts==1.2.0 depends on piper-phonemize, which only
# publishes wheels up to cp310. On 3.11/3.12 pip fails with
# "No matching distribution found for piper-phonemize". Pin to 3.10 here.
FROM python:3.10-slim

WORKDIR /app

# ffmpeg libs back PyAV's container decode; curl fetches the baked voice model.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "fastapi>=0.110" \
    "uvicorn[standard]>=0.29" \
    "faster-whisper>=1.0" \
    "piper-tts==1.2.0" \
    "python-multipart>=0.0.9" \
    "prometheus-client>=0.20"

# --- Bake the models into the image so the first deploy needs no network -----
# (same discipline as Phase 22: first deploy must be immediately testable).

# 1) Piper British male voice (en_GB-alan-medium) — the "Jarvis butler" voice.
ENV PIPER_VOICE=en_GB-alan-medium
ENV PIPER_VOICES_DIR=/app/voices
RUN mkdir -p /app/voices && \
    BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium" && \
    curl -fsSL -o /app/voices/${PIPER_VOICE}.onnx       "${BASE}/${PIPER_VOICE}.onnx" && \
    curl -fsSL -o /app/voices/${PIPER_VOICE}.onnx.json  "${BASE}/${PIPER_VOICE}.onnx.json"

# 2) faster-whisper base.en (CT2 int8) — pre-download into the image cache.
ENV HF_HOME=/app/hf
ENV WHISPER_MODEL=base.en
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')"

COPY main.py .
COPY metrics.py .

EXPOSE 8000
CMD ["python", "main.py"]
