# Phase 24: Voice mode (speech in, speech out + JARVIS orb)

**Status:** Implemented and deployed (2026-06-24)
**Depends on:** Phase 2 (agent `/chat`), Phase 13 (Cloudflare Tunnel),
Phase 22 (shared `metrics.py`)

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

## Remote access (Cloudflare Tunnel + Access)

The mic was the forcing function here. `getUserMedia` is hard-blocked by every
browser on any origin that isn't `https://` or `localhost`, so plain
`http://athena.local` can load the page but **never** the mic. Rather than mint a
LAN TLS cert, we reused the **existing Phase 13 Cloudflare Tunnel** — which solves
the mic *and* off-LAN access in one move:

- **Published-application route** on the `athena-mcp` tunnel:
  `athena.athenamcp.uk` → `http://frontend.athena.svc.cluster.local:80` (HTTP —
  the cloudflared pod is in-namespace and nginx serves plain :80; Cloudflare
  terminates TLS at its edge with a trusted public cert). Three hops: browser↔edge
  is HTTPS (the secure context that unblocks the mic), edge↔cloudflared is the
  encrypted tunnel, cloudflared↔frontend is in-cluster HTTP.
- **Cloudflare Access** gates the hostname: a self-hosted Access app on
  `athena.athenamcp.uk` with a single **Allow / Include / Emails =
  `varun.anand2006@gmail.com`** policy, **one-time-PIN** login. Default-deny —
  everyone else is refused at the edge before traffic reaches the cluster.

**Why the gate is mandatory:** the frontend/agent has **no app-level auth**. The
`/chat`, `/memory`, `/documents`, `/conversations` paths are wide open by design
(LAN-only until now), and the agent can read the memory vault, read documents, and
do Phase 23 calendar writes. Publishing without Access would expose all of that to
anyone with the URL. Access is currently the *only* auth layer in front of a
public Athena — see "Open items".

The tunnel routing/auth is managed in the **Cloudflare dashboard** (token-based
connector), so none of it lives in the repo — there is nothing to `kubectl apply`
for the exposure. The LAN path (`http://athena.local` via Traefik) is unchanged:
still HTTP, still unauthenticated, still no mic.

## Gotchas

- **Build: Piper needs Python 3.10.** `piper-tts==1.2.0` depends on
  `piper-phonemize`, which publishes **no wheels past CPython 3.10** — a 3.12 base
  image fails with `No matching distribution found for piper-phonemize`. The voice
  image is pinned to `python:3.10-slim` (and `requires-python = ">=3.10,<3.11"`).
  Import is `from piper.voice import PiperVoice`. Cost one build cycle to find.
- **Mic needs a secure context — resolved by the tunnel.** Off `localhost` the mic
  only works over HTTPS. `https://athena.athenamcp.uk` (Cloudflare edge cert) is
  the supported path and works on every device. `http://athena.local` on the LAN
  still cannot use the mic.
- **Cloudflare ≠ app auth.** The gate is at the edge only. Don't add a second
  public route that bypasses Access, and don't broaden the email allow-list
  casually.
- **"Exactly Jarvis" isn't literal.** The real Jarvis voice (Paul Bettany) is a
  copyrighted performance — not clonable. `en_GB-alan-medium` is the closest
  self-hosted "British AI butler" stand-in; a future XTTS path could get closer.

## Deployment (as shipped)

1. Build `athena-voice:phase24` on xdev-sr (`python:3.10-slim` base — see gotcha);
   `k3s ctr images import` on xdev-sr (the pod runs there).
2. Build `athena-frontend:phase24` on xdev-sr; SCP the tar to vlinux2 and
   `k3s ctr images import` there (the frontend pod runs on vlinux2).
3. From vlinux1: `kubectl apply -f cluster/voice/ -f cluster/frontend/deployment.yaml`
   then `kubectl rollout restart deploy/voice deploy/frontend -n athena`.
4. Cloudflare dashboard (one-time, not in repo): add the published-application
   route + the Access app/policy described above.

No DB migration, no new secret, no new OAuth scope.

## Open items

- **App-level auth.** Remote exposure leans entirely on Cloudflare Access. Worth
  deciding whether the app/agent should grow real authentication rather than
  relying solely on the edge gate — especially before any second public route or
  widening the allow-list.
- **LAN mic.** Still no mic on `http://athena.local`; only the tunnel hostname is
  a secure context. A LAN TLS cert would fix it if wanted.

## Explicitly deferred

- XTTS-v2 voice cloning (needs a GPU).
- Wake-word / always-listening (push-to-talk only for v1).
- Streaming STT/TTS (clip-at-a-time round trips for v1).
- Exposing voice via the Rust MCP server (LAN frontend only).
