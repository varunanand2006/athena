# Phase 24: Voice mode (speech in, speech out + JARVIS orb)

**Status:** Implemented, pending cluster rollout
**Depends on:** Phase 2 (agent `/chat`), Phase 22 (shared `metrics.py`)

## Goal

Give Athena a voice. A full-screen, JARVIS-style **voice mode**: a glowing,
pulsating orb you talk to. Push-to-talk — tap the orb (or hold `Space`) to
speak; Athena transcribes, thinks with the existing agent, and speaks the reply
back in a British "AI butler" voice. The orb pulses to live audio amplitude:
your voice while it listens, Athena's while it speaks.

Everything runs on the cluster. No cloud STT/TTS — the same self-hosted
discipline as the rest of Athena.

## Design

### A thin I/O shell around the existing agent

Voice adds **no intelligence** — it wraps the agent's brain in audio:

```
mic ──► POST /stt ──► POST /chat ──► POST /tts ──► speaker
        (whisper)     (existing)      (Piper)
```

The agent, LangGraph graph, memory, tools, and prompts are all untouched. No new
LLM scope, no new secret. A voice turn is just a normal `/chat` turn with audio
on either end, so it persists to the same Postgres conversation history and
triggers the same reflection.

### New `voice/` service (FastAPI, on xdev-sr)

`voice/main.py` exposes three routes, models loaded once in the lifespan and
reused warm per request:

- `POST /stt` — multipart `audio` (browser `MediaRecorder` WebM/Opus) →
  `{"text": ...}`. **faster-whisper** `base.en` at `int8`. faster-whisper decodes
  the WebM/Opus container itself via PyAV, so there's no ffmpeg shell-out in the
  request path.
- `POST /tts` — `{"text": ...}` → `audio/wav`. **Piper**, voice
  `en_GB-alan-medium` (British male — the "butler" timbre).
- `GET /healthz` — `200` once both engines are loaded, `503` while loading
  (gates the readiness probe so traffic waits for warm models).

Conventions match the other Python services: copied `metrics.py`,
`prometheus-client` in both `pyproject.toml` **and** the Dockerfile pip list,
`SERVICE_NAME=voice`. STT/TTS latency is timed with `track_job("stt"|"tts")`;
an empty transcript / empty synthesis fires `job_empty_result(...)` + a
`level=warning` JSON log line, so the silent-failure class is loud on the Athena
Overview dashboard (Phase 22).

### Why Piper + whisper base.en (the CPU reality)

The cluster has **no GPU** (xdev-sr is the AI node, CPU-only — the same wall that
retired Gemma). That picks the engines:

- **Piper** is fast and genuinely good on CPU with low latency — the pragmatic
  "ships today" choice. True voice cloning (Coqui XTTS-v2) toward a bespoke
  Jarvis timbre is sluggish on CPU and really wants a GPU; deferred until/if a
  GPU is added. The service is structured so the TTS engine can be swapped behind
  `/tts` without touching the frontend.
- **whisper `base.en` int8** keeps transcription responsive. Bump to `small.en`
  via the `WHISPER_MODEL` env if accuracy matters more than latency.

### Models baked into the image

Both models are baked at build time (Dockerfile `RUN curl` for the Piper
`.onnx` + a pre-download of the whisper CT2 model), so the **first deploy is
immediately testable with no runtime download** — same discipline as Phase 22.
Build on xdev-sr, import with `k3s ctr` directly (the pod runs on xdev-sr).

### Full-screen voice mode + orb (frontend)

New `/voice` route (`VoiceView.tsx`) + a Sidebar nav entry. Push-to-talk via
click-toggle or hold-`Space`. The orb (`.voice-orb` in `globals.css`) reads a
`--level` CSS variable set every animation frame from a Web Audio `AnalyserNode`
RMS — mic amplitude while listening, playback amplitude while speaking — driving
its scale and glow. State tints: blue idle/speaking, teal listening, fast pulse
thinking, red on error.

nginx proxies `/stt` and `/tts` to the voice service (`proxy_read_timeout 300s`
for slow CPU transcription).

## Gotchas

- **Mic needs a secure context.** `getUserMedia` is blocked on plain HTTP except
  on `localhost`. Over `athena.local` (plain HTTP) the browser will refuse the
  mic — the UI detects `!window.isSecureContext` and says so. Fix at rollout:
  serve the frontend over HTTPS (or use `localhost`/a TLS ingress). Tracked as
  the one open item before this is usable off the dev box.
- **"Exactly Jarvis" isn't literal.** The real Jarvis voice (Paul Bettany) is a
  copyrighted performance — not clonable. `en_GB-alan-medium` is the closest
  self-hosted "British AI butler" stand-in; a future XTTS path could get closer.

## Deployment

1. Build `athena-voice:phase24` on xdev-sr; `k3s ctr images import` on xdev-sr.
2. `kubectl apply -f cluster/voice/` (deployment + service).
3. Rebuild + redeploy the frontend (new `/voice` route, nginx `/stt`+`/tts`
   proxy).
4. Resolve the secure-context item so the mic works off `localhost`.

No DB migration, no new secret, no new OAuth scope.

## Explicitly deferred

- XTTS-v2 voice cloning (needs a GPU).
- Wake-word / always-listening (push-to-talk only for v1).
- Streaming STT/TTS (clip-at-a-time round trips for v1).
- Exposing voice via the Rust MCP server (LAN frontend only).
