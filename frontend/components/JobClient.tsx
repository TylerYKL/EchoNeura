"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";

import { AudioPlayer } from "@/components/AudioPlayer";
import { ExportPanel } from "@/components/ExportPanel";
import { JobProgress } from "@/components/JobProgress";
import { SpeakerPanel } from "@/components/SpeakerPanel";
import { StatusBadge } from "@/components/StatusBadge";
import { TranscriptView } from "@/components/TranscriptView";
import { api } from "@/lib/api";
import { formatAbsolute, formatBytes, formatDuration, formatRelative } from "@/lib/format";
import { useJob } from "@/lib/hooks";
import { TERMINAL_STATUSES } from "@/lib/types";

export function JobClient({ jobId }: { jobId: string }) {
  const router = useRouter();
  const { job, error, loading, refresh } = useJob(jobId);
  const [currentTime, setCurrentTime] = useState(0);
  const [seekTo, setSeekTo] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const seek = useCallback((seconds: number) => {
    setSeekTo(seconds);
    // Reset so clicking the same timestamp twice still seeks.
    setTimeout(() => setSeekTo(null), 60);
  }, []);

  const run = useCallback(
    async (label: string, action: () => Promise<unknown>) => {
      setBusy(label);
      setActionError(null);
      try {
        await action();
        await refresh();
      } catch (err) {
        setActionError(err instanceof Error ? err.message : `${label} failed`);
      } finally {
        setBusy(null);
      }
    },
    [refresh],
  );

  if (loading && !job) {
    return (
      <div className="mt-8 space-y-4">
        <div className="card h-24 animate-pulse-soft" />
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="card h-[520px] animate-pulse-soft" />
          <div className="space-y-4">
            <div className="card h-40 animate-pulse-soft" />
            <div className="card h-40 animate-pulse-soft" />
          </div>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="mt-10">
        <div className="card p-6">
          <h1 className="text-lg font-semibold text-white">Job not found</h1>
          <p className="mt-2 text-sm text-[var(--color-mute)]">
            {error ?? `There is no job with id “${jobId}”. It may have been deleted.`}
          </p>
          <a href="/" className="btn btn-primary mt-4">
            ← Back to studio
          </a>
        </div>
      </div>
    );
  }

  const transcript = job.transcript;
  const done = job.status === "completed";
  const active = !TERMINAL_STATUSES.includes(job.status);

  const copyTranscript = async () => {
    try {
      const text = await api.transcriptText(job.id);
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not copy transcript");
    }
  };

  return (
    <div className="mt-6 space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <a href="/" className="btn btn-ghost btn-sm">
          ← Studio
        </a>
        <span className="text-[var(--color-mute)]">/</span>
        <span className="max-w-[40ch] truncate font-medium text-white">{job.original_filename}</span>
        <StatusBadge status={job.status} />
      </div>

      <header className="card p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="truncate text-xl font-semibold tracking-tight text-white">
              {job.original_filename}
            </h1>
            <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--color-mute)]">
              <span>{formatBytes(job.file_size_bytes)}</span>
              <span aria-hidden>·</span>
              <span>{formatDuration(job.audio_duration_seconds)}</span>
              <span aria-hidden>·</span>
              <span>
                {job.language === "auto" ? "auto-detect" : job.language.toUpperCase()}
                {job.detected_language && job.detected_language !== job.language
                  ? ` → ${job.detected_language.toUpperCase()}`
                  : ""}
              </span>
              <span aria-hidden>·</span>
              <span>{formatRelative(job.created_at)}</span>
              {job.asr_provider && (
                <>
                  <span aria-hidden>·</span>
                  <span className="font-mono">{job.asr_provider}</span>
                </>
              )}
              {job.diarization_provider && (
                <>
                  <span aria-hidden>·</span>
                  <span className="font-mono">diarization: {job.diarization_provider}</span>
                </>
              )}
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            {active && (
              <button
                type="button"
                className="btn btn-sm"
                disabled={busy !== null}
                onClick={() => void run("cancel", () => api.cancelJob(job.id))}
              >
                {busy === "cancel" ? "Cancelling…" : "Cancel"}
              </button>
            )}
            {!active && (
              <button
                type="button"
                className="btn btn-sm"
                disabled={busy !== null}
                title="Re-run the pipeline from scratch (discards edits)"
                onClick={() => void run("reprocess", () => api.reprocessJob(job.id))}
              >
                {busy === "reprocess" ? "Re-queueing…" : "Reprocess"}
              </button>
            )}
            {done && transcript && (
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => void copyTranscript()}
              >
                {copied ? "Copied ✓" : "Copy transcript"}
              </button>
            )}
            <button
              type="button"
              className="btn btn-sm text-[#fca5a5] hover:border-[#5a2226] hover:bg-[#1a0f11]"
              disabled={busy !== null}
              onClick={() => {
                if (
                  window.confirm(
                    `Delete “${job.original_filename}” and its transcript? The audio file is removed from disk too.`,
                  )
                ) {
                  void run("delete", async () => {
                    await api.deleteJob(job.id);
                    router.push("/");
                    router.refresh();
                  });
                }
              }}
            >
              {busy === "delete" ? "Deleting…" : "Delete"}
            </button>
          </div>
        </div>

        {(actionError || error) && (
          <p
            role="alert"
            className="mt-4 rounded-lg border border-[#5a2226] bg-[#1a0f11] px-3 py-2 text-xs text-[#fca5a5]"
          >
            {actionError ?? error}
          </p>
        )}

        {!done && (
          <div className="mt-5">
            <JobProgress job={job} />
          </div>
        )}
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
        <div className="space-y-4">
          <AudioPlayer
            jobId={job.id}
            filename={job.original_filename}
            durationSeconds={job.audio_duration_seconds}
            seekTo={seekTo}
            onTimeUpdate={setCurrentTime}
          />

          {done && transcript ? (
            <TranscriptView
              jobId={job.id}
              segments={transcript.segments}
              speakers={transcript.speakers}
              currentTime={currentTime}
              onSeek={seek}
              onChanged={() => void refresh()}
            />
          ) : job.status === "failed" ? (
            <div className="card p-5">
              <h3 className="text-sm font-semibold text-white">This job failed</h3>
              <p className="mt-2 text-sm text-[var(--color-mute)]">
                The error above is written straight from the provider. The two most common causes
                are a missing API key for the selected provider, or an audio file the ASR vendor
                refused.
              </p>
              <button
                type="button"
                className="btn btn-primary mt-4"
                onClick={() => void run("reprocess", () => api.reprocessJob(job.id))}
              >
                Try again
              </button>
            </div>
          ) : (
            <div className="card p-8 text-center">
              <p className="text-sm text-[var(--color-mute)]">
                Transcript appears here as soon as processing finishes.
              </p>
            </div>
          )}

          {job.events.length > 0 && (
            <details className="card p-4" open={job.status === "failed"}>
              <summary className="cursor-pointer text-sm font-semibold text-white">
                Pipeline log
                <span className="ml-2 text-xs font-normal text-[var(--color-mute)]">
                  {job.events.length} entries
                </span>
              </summary>
              <ol className="mt-3 space-y-1.5 font-mono text-[11px] leading-relaxed">
                {job.events.map((event, index) => (
                  <li key={index} className="flex gap-2">
                    <span className="shrink-0 text-[#5f6779]">
                      {formatAbsolute(event.created_at)}
                    </span>
                    <span
                      className={[
                        "shrink-0 rounded px-1",
                        event.level === "error"
                          ? "bg-[#2a1416] text-[#fca5a5]"
                          : event.level === "warn"
                            ? "bg-[#211d14] text-[#d6c08a]"
                            : "bg-[var(--color-ink-800)] text-[var(--color-mute)]",
                      ].join(" ")}
                    >
                      {event.stage}
                    </span>
                    <span className="min-w-0 break-words text-[#c6ccdb]">{event.message}</span>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </div>

        <aside className="space-y-4">
          {transcript && (
            <SpeakerPanel
              jobId={job.id}
              speakers={transcript.speakers}
              onChanged={() => void refresh()}
            />
          )}
          <ExportPanel jobId={job.id} disabled={!done || !transcript} />
        </aside>
      </div>
    </div>
  );
}
