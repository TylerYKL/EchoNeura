"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { voiceApi } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { VoiceUtterance, VoiceWsMessage } from "@/lib/types";
import { voiceWsCandidates } from "@/lib/voice-ws";

type ConnectionState = "idle" | "connecting" | "open" | "error";
type RecState = "idle" | "recording" | "processing";

const TARGET_SAMPLE_RATE = 16_000;

/**
 * Hold-to-talk live voice console.
 *
 * Mic -> AudioWorklet (Float32 -> Int16 PCM) -> WebSocket frames -> EchoNeura
 * ASR + assistant -> transcript & reply back over the same socket. This is the
 * same protocol a rooted Echo Dot or a Pi satellite would speak
 * (docs/device/voice-protocol.md), so this page doubles as the reference client.
 */
export function VoiceClient() {
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [recState, setRecState] = useState<RecState>("idle");
  const [result, setResult] = useState<VoiceUtterance | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<VoiceUtterance[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [wsUrl, setWsUrl] = useState<string | null>(null);
  const [micBlocked, setMicBlocked] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const nodesRef = useRef<{ source?: MediaStreamAudioSourceNode; processor?: AudioWorkletNode; sink?: GainNode }>({});
  const queueRef = useRef<ArrayBuffer[]>([]);
  const connectPromiseRef = useRef<Promise<WebSocket> | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const resultWaitersRef = useRef<Array<(m: VoiceWsMessage) => void>>([]);

  const refreshHistory = useCallback(async () => {
    try {
      const data = await voiceApi.history(15);
      setHistory(data.items);
    } catch {
      /* history is non-critical */
    }
  }, []);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  useEffect(
    () => () => {
      // Never leave the mic hot or the socket dangling on unmount.
      streamRef.current?.getTracks().forEach((t) => t.stop());
      try {
        wsRef.current?.close();
      } catch {
        /* already closed */
      }
      if (timerRef.current) clearInterval(timerRef.current);
      void audioCtxRef.current?.close().catch(() => undefined);
    },
    [],
  );

  const openSocket = useCallback(async (): Promise<WebSocket> => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return wsRef.current;
    if (connectPromiseRef.current) return connectPromiseRef.current;

    setConnection("connecting");
    setError(null);

    const rate = audioCtxRef.current?.sampleRate ?? TARGET_SAMPLE_RATE;
    const candidates = voiceWsCandidates(rate, "auto");

    const attempt = (index: number): Promise<WebSocket> =>
      new Promise<WebSocket>((resolve, reject) => {
        if (index >= candidates.length) {
          reject(new Error("Could not reach the voice endpoint on any candidate URL"));
          return;
        }
        const url = candidates[index];
        const socket = new WebSocket(url);
        socket.binaryType = "arraybuffer";
        const timeout = setTimeout(() => {
          try {
            socket.close();
          } catch {
            /* noop */
          }
        }, 6000);

        socket.onopen = () => {
          clearTimeout(timeout);
          wsRef.current = socket;
          setWsUrl(url);
          setConnection("open");
          resolve(socket);
        };
        socket.onerror = () => {
          clearTimeout(timeout);
          // Try the next candidate (e.g. proxy without WS support -> direct).
          socket.onclose = null;
          try {
            socket.close();
          } catch {
            /* noop */
          }
          attempt(index + 1).then(resolve, reject);
        };
        socket.onclose = () => {
          clearTimeout(timeout);
          if (wsRef.current === socket) {
            wsRef.current = null;
            setConnection("idle");
          }
        };
        socket.onmessage = (event) => {
          let message: VoiceWsMessage;
          try {
            message = JSON.parse(String(event.data)) as VoiceWsMessage;
          } catch {
            return;
          }
          if (message.type === "ready") return;
          const waiters = resultWaitersRef.current;
          resultWaitersRef.current = [];
          waiters.forEach((w) => w(message));
          if (message.type === "result") {
            setResult(message.utterance);
            setRecState("idle");
            setNotice(null);
            void refreshHistory();
          } else if (message.type === "discarded") {
            setNotice(`Discarded (${message.reason})`);
            setRecState("idle");
          } else if (message.type === "error") {
            setError(message.message);
            if (message.fatal) setRecState("idle");
          }
        };
      });

    connectPromiseRef.current = attempt(0).finally(() => {
      connectPromiseRef.current = null;
    });
    try {
      return await connectPromiseRef.current;
    } catch (err) {
      setConnection("error");
      throw err;
    }
  }, [refreshHistory]);

  const startRecording = useCallback(async () => {
    if (recState !== "idle") return;
    setError(null);
    setNotice(null);
    setResult(null);

    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        setMicBlocked(true);
        setError("This browser/context does not expose microphone capture (needs HTTPS or localhost).");
        return;
      }
      if (!audioCtxRef.current) {
        try {
          audioCtxRef.current = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
        } catch {
          audioCtxRef.current = new AudioContext(); // Safari may reject the hint
        }
      }
      const ctx = audioCtxRef.current;
      await ctx.resume();

      const socket = await openSocket();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;

      await ctx.audioWorklet.addModule("/worklets/pcm-recorder.js");
      const source = ctx.createMediaStreamSource(stream);
      const processor = new AudioWorkletNode(ctx, "pcm-recorder");
      const sink = ctx.createGain();
      sink.gain.value = 0; // worklet must reach the destination to be pulled; keep it silent
      source.connect(processor);
      processor.connect(sink);
      sink.connect(ctx.destination);
      nodesRef.current = { source, processor, sink };

      processor.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(event.data);
        else queueRef.current.push(event.data);
      };
      queueRef.current.forEach((buf) => socket.send(buf));
      queueRef.current = [];

      setRecState("recording");
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((e) => e + 0.1), 100);
    } catch (err) {
      setRecState("idle");
      if (err instanceof DOMException && (err.name === "NotAllowedError" || err.name === "SecurityError")) {
        setMicBlocked(true);
        setError("Microphone permission denied. Allow it in your browser, then try again.");
      } else {
        setError(err instanceof Error ? err.message : "Could not start recording");
      }
    }
  }, [openSocket, recState]);

  const stopRecording = useCallback(() => {
    if (recState !== "recording") return;
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    // Release the mic immediately — the indicator light matters for trust.
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    const { processor, source, sink } = nodesRef.current;
    try {
      processor?.disconnect();
      source?.disconnect();
      sink?.disconnect();
    } catch {
      /* already torn down */
    }
    nodesRef.current = {};

    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setRecState("idle");
      setError("Connection dropped while recording — nothing was sent.");
      return;
    }
    setRecState("processing");
    socket.send(JSON.stringify({ type: "stop" }));
    // Safety net: if the server never answers, don't hang in "processing".
    resultWaitersRef.current.push(() => undefined);
    setTimeout(() => {
      setRecState((state) => (state === "processing" ? "idle" : state));
    }, 20_000);
  }, [recState]);

  const recording = recState === "recording";
  const busy = recState !== "idle";

  return (
    <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start">
      <section className="card p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-white">Live voice</h2>
            <p className="mt-1 text-sm text-[var(--color-mute)]">
              Hold the button and speak. Audio streams to EchoNeura, comes back as text plus an
              assistant action.
            </p>
          </div>
          <span
            className={[
              "chip",
              connection === "open"
                ? "border-[#1d4436] bg-[#12251c] text-[#6ee7b7]"
                : connection === "connecting"
                  ? "border-[#2e3563] bg-[#1b1f3a] text-[#a5b4fc] animate-pulse-soft"
                  : connection === "error"
                    ? "border-[#5a2226] bg-[#1a0f11] text-[#fca5a5]"
                    : "",
            ].join(" ")}
          >
            <span
              aria-hidden
              className={`size-1.5 rounded-full ${
                connection === "open"
                  ? "bg-[#10b981]"
                  : connection === "connecting"
                    ? "bg-[#818cf8]"
                    : connection === "error"
                      ? "bg-[#ef4444]"
                      : "bg-[#3a4256]"
              }`}
            />
            {connection === "open"
              ? "stream connected"
              : connection === "connecting"
                ? "connecting…"
                : connection === "error"
                  ? "connection failed"
                  : "stream idle"}
          </span>
        </div>

        <div className="mt-8 flex flex-col items-center gap-4 py-6">
          <button
            type="button"
            onPointerDown={() => void startRecording()}
            onPointerUp={stopRecording}
            onPointerLeave={() => recording && stopRecording()}
            onPointerCancel={() => recording && stopRecording()}
            onKeyDown={(event) => {
              if ((event.key === " " || event.key === "Enter") && !event.repeat) {
                event.preventDefault();
                void startRecording();
              }
            }}
            onKeyUp={(event) => {
              if (event.key === " " || event.key === "Enter") stopRecording();
            }}
            disabled={busy && !recording}
            aria-label="Hold to talk"
            className={[
              "grid size-32 select-none place-items-center rounded-full text-sm font-semibold transition touch-none",
              recording
                ? "bg-[#ef4444] text-white shadow-[0_0_60px_-10px_#ef4444] scale-105 animate-pulse-soft"
                : recState === "processing"
                  ? "bg-[var(--color-ink-700)] text-[var(--color-mute)]"
                  : "bg-[var(--color-accent)] text-white shadow-[0_0_50px_-12px_var(--color-accent)] hover:bg-[var(--color-accent-soft)] active:scale-95",
            ].join(" ")}
          >
            {recording ? "Release to send" : recState === "processing" ? "Processing…" : "Hold to talk"}
          </button>
          <p className="font-mono text-xs text-[var(--color-mute)] tabular-nums">
            {recording ? `${elapsed.toFixed(1)}s` : recState === "processing" ? "transcribing + assistant…" : "or focus and hold Space"}
          </p>
        </div>

        {error && (
          <p role="alert" className="rounded-lg border border-[#5a2226] bg-[#1a0f11] px-3 py-2 text-sm text-[#fca5a5]">
            {error}
          </p>
        )}
        {notice && !error && (
          <p className="rounded-lg border border-[#463a1e] bg-[#1a1710] px-3 py-2 text-sm text-[#e8d9a8]">{notice}</p>
        )}
        {micBlocked && (
          <p className="mt-2 text-xs text-[var(--color-mute)]">
            Microphone capture requires a secure context: <code>localhost</code>, the sandbox
            preview (https), or any HTTPS origin.
          </p>
        )}

        {result && (
          <div className="mt-4 animate-rise space-y-3">
            <div className="rounded-xl border border-[var(--color-line-soft)] bg-[var(--color-ink-900)] p-4">
              <p className="label mb-1.5">You said</p>
              <p className="text-sm leading-relaxed text-white">{result.transcript || "(nothing intelligible)"}</p>
              <p className="mt-2 font-mono text-[11px] text-[var(--color-mute)]">
                {result.audio_seconds.toFixed(1)}s audio · {result.processing_ms}ms ·{" "}
                {result.asr_provider}
                {result.detected_language ? ` · ${result.detected_language}` : ""}
              </p>
            </div>
            <div className="rounded-xl border border-[#2e3563] bg-[#141733] p-4">
              <p className="label mb-1.5">
                Assistant <span className="normal-case tracking-normal">({result.assistant.provider})</span>
              </p>
              <p className="text-sm leading-relaxed text-white">{result.assistant.reply}</p>
              <div className="mt-2 flex items-center gap-2">
                <span className="chip border-[#2e3563] bg-[#1b1f3a] text-[#a5b4fc]">
                  action: {result.assistant.action}
                </span>
                {Object.keys(result.assistant.data ?? {}).length > 0 && (
                  <span className="chip font-mono">{JSON.stringify(result.assistant.data).slice(0, 60)}</span>
                )}
              </div>
            </div>
          </div>
        )}

        {wsUrl && (
          <p className="mt-4 truncate font-mono text-[10px] text-[#5f6779]" title={wsUrl}>
            ws: {wsUrl}
          </p>
        )}
      </section>

      <aside className="card p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white">Recent utterances</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void refreshHistory()}>
            refresh
          </button>
        </div>
        {history.length === 0 ? (
          <p className="text-sm text-[var(--color-mute)]">
            Nothing yet. Hold to talk, or stream a file with{" "}
            <code className="font-mono text-xs">tools/stream_audio_to_voice.py</code>.
          </p>
        ) : (
          <ul className="space-y-2">
            {history.map((u) => (
              <li key={u.id} className="rounded-lg border border-[var(--color-line-soft)] bg-[var(--color-ink-900)] p-3">
                <p className="line-clamp-2 text-sm text-white">{u.transcript || "(empty)"}</p>
                <p className="mt-1.5 line-clamp-2 text-xs text-[var(--color-mute)]">
                  ↳ {u.assistant.reply}
                </p>
                <div className="mt-2 flex items-center gap-2 text-[10px] text-[#5f6779]">
                  <span className="chip">{u.source}</span>
                  <span>{formatRelative(u.created_at)}</span>
                  <span aria-hidden>·</span>
                  <span>{u.audio_seconds.toFixed(1)}s</span>
                  {u.assistant.action && u.assistant.action !== "none" && (
                    <span className="chip border-[#2e3563] bg-[#1b1f3a] text-[#a5b4fc]">{u.assistant.action}</span>
                  )}
                  <button
                    type="button"
                    className="ml-auto text-[#5f6779] transition hover:text-[#fca5a5]"
                    onClick={async () => {
                      await voiceApi.remove(u.id);
                      void refreshHistory();
                    }}
                    aria-label="Delete utterance"
                  >
                    ✕
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </div>
  );
}
