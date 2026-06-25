import { useCallback, useEffect, useRef, useState } from 'react'

// ─── Voice mode ─────────────────────────────────────────────────────────────
// Full-screen JARVIS-style voice interface. Push-to-talk: click the orb (or hold
// Space) to talk. The pipeline is a thin shell around the existing agent —
//   mic  ──► POST /stt  ──► POST /chat  ──► POST /tts  ──► speaker
// The orb pulses to live audio amplitude: your voice while listening, Athena's
// while speaking (Web Audio AnalyserNode → a --level CSS variable).

type VoiceState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error'

const STATE_LABEL: Record<VoiceState, string> = {
  idle: 'Tap to speak',
  listening: 'Listening…',
  thinking: 'Thinking…',
  speaking: 'Speaking…',
  error: 'Something went wrong',
}

export default function VoiceView() {
  const [state, setState] = useState<VoiceState>('idle')
  const [transcript, setTranscript] = useState('')
  const [reply, setReply] = useState('')
  const [error, setError] = useState<string | null>(null)

  const orbRef = useRef<HTMLDivElement>(null)
  const conversationIdRef = useRef<string | null>(null)

  // Web Audio plumbing, kept in refs so the rAF loop and handlers share them.
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const rafRef = useRef<number | null>(null)
  const levelRef = useRef(0)

  // MediaRecorder state.
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const micStreamRef = useRef<MediaStream | null>(null)

  // ── Amplitude → orb glow ──────────────────────────────────────────────────
  const startMeter = useCallback(() => {
    if (rafRef.current != null) return
    const analyser = analyserRef.current
    if (!analyser) return
    const data = new Uint8Array(analyser.frequencyBinCount)
    const tick = () => {
      analyser.getByteTimeDomainData(data)
      // RMS around the 128 midpoint → 0..1, then smooth for a fluid feel.
      let sum = 0
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128
        sum += v * v
      }
      const rms = Math.sqrt(sum / data.length)
      levelRef.current = levelRef.current * 0.8 + Math.min(1, rms * 3.5) * 0.2
      orbRef.current?.style.setProperty('--level', levelRef.current.toFixed(3))
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }, [])

  const stopMeter = useCallback(() => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
    rafRef.current = null
    levelRef.current = 0
    orbRef.current?.style.setProperty('--level', '0')
  }, [])

  const ensureAudioCtx = useCallback(() => {
    if (!audioCtxRef.current) {
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      audioCtxRef.current = new Ctx()
    }
    return audioCtxRef.current
  }, [])

  // ── Speak a reply (TTS → playback, orb driven by output amplitude) ────────
  const speak = useCallback(
    async (text: string) => {
      const res = await fetch('/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      if (!res.ok) throw new Error('tts failed')
      const arrayBuf = await res.arrayBuffer()

      const ctx = ensureAudioCtx()
      const audioBuf = await ctx.decodeAudioData(arrayBuf)
      const source = ctx.createBufferSource()
      source.buffer = audioBuf

      const analyser = ctx.createAnalyser()
      analyser.fftSize = 512
      source.connect(analyser)
      analyser.connect(ctx.destination)
      analyserRef.current = analyser

      setState('speaking')
      startMeter()
      source.start()
      await new Promise<void>((resolve) => {
        source.onended = () => resolve()
      })
      stopMeter()
    },
    [ensureAudioCtx, startMeter, stopMeter]
  )

  // ── Stop recording → STT → chat → TTS ─────────────────────────────────────
  const handleRecordingStop = useCallback(async () => {
    micStreamRef.current?.getTracks().forEach((t) => t.stop())
    micStreamRef.current = null
    stopMeter()

    const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
    chunksRef.current = []
    if (blob.size === 0) {
      setState('idle')
      return
    }

    try {
      setState('thinking')

      // 1) Speech → text
      const form = new FormData()
      form.append('audio', blob, 'clip.webm')
      const sttRes = await fetch('/stt', { method: 'POST', body: form })
      if (!sttRes.ok) throw new Error('stt failed')
      const { text } = (await sttRes.json()) as { text: string }
      setTranscript(text)
      if (!text) {
        setState('idle')
        return
      }

      // 2) Text → agent
      const chatRes = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, conversation_id: conversationIdRef.current }),
      })
      if (!chatRes.ok) throw new Error('chat failed')
      const chat = (await chatRes.json()) as { response: string; conversation_id: string }
      conversationIdRef.current = chat.conversation_id
      setReply(chat.response)

      // 3) Reply → speech
      await speak(chat.response)
      setState('idle')
    } catch (err) {
      console.error(err)
      setError('Athena is unreachable — check the voice and agent pods.')
      setState('error')
    }
  }, [speak, stopMeter])

  // ── Start recording (mic → recorder + live meter) ─────────────────────────
  const startRecording = useCallback(async () => {
    setError(null)
    setTranscript('')
    setReply('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      micStreamRef.current = stream

      const ctx = ensureAudioCtx()
      if (ctx.state === 'suspended') await ctx.resume()
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 512
      ctx.createMediaStreamSource(stream).connect(analyser)
      analyserRef.current = analyser

      const recorder = new MediaRecorder(stream)
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = handleRecordingStop
      recorderRef.current = recorder
      recorder.start()

      setState('listening')
      startMeter()
    } catch (err) {
      console.error(err)
      // getUserMedia needs a secure context (HTTPS or localhost).
      setError(
        !window.isSecureContext
          ? 'Microphone needs a secure context — open Athena over HTTPS or via localhost.'
          : 'Microphone access was denied.'
      )
      setState('error')
    }
  }, [ensureAudioCtx, handleRecordingStop, startMeter])

  const stopRecording = useCallback(() => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
  }, [])

  // ── Push-to-talk toggle ───────────────────────────────────────────────────
  const toggle = useCallback(() => {
    if (state === 'listening') stopRecording()
    else if (state === 'idle' || state === 'error') startRecording()
    // ignore taps while thinking / speaking
  }, [state, startRecording, stopRecording])

  // Spacebar push-to-talk (hold to talk).
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === 'Space' && !e.repeat) {
        e.preventDefault()
        if (state === 'idle' || state === 'error') startRecording()
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.code === 'Space') {
        e.preventDefault()
        if (state === 'listening') stopRecording()
      }
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [state, startRecording, stopRecording])

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
      micStreamRef.current?.getTracks().forEach((t) => t.stop())
      audioCtxRef.current?.close()
    }
  }, [])

  const busy = state === 'thinking' || state === 'speaking'

  return (
    <div className="flex flex-col items-center justify-center h-full w-full select-none px-6">
      <button
        type="button"
        onClick={toggle}
        disabled={busy}
        aria-label={STATE_LABEL[state]}
        className="voice-orb-button"
      >
        <div ref={orbRef} className={`voice-orb voice-orb--${state}`}>
          <div className="voice-orb-glow" />
          <div className="voice-orb-core" />
          <div className="voice-orb-ring" />
        </div>
      </button>

      <p className="voice-status mt-12">{STATE_LABEL[state]}</p>

      {transcript && (
        <p className="voice-transcript mt-6">“{transcript}”</p>
      )}
      {reply && state !== 'listening' && (
        <p className="voice-reply mt-3">{reply}</p>
      )}
      {error && <p className="voice-error mt-6">{error}</p>}

      <p className="voice-hint mt-10">
        Tap the orb or hold <kbd>Space</kbd> to talk
      </p>
    </div>
  )
}
