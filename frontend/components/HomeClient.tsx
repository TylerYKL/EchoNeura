"use client";

import { useRouter } from "next/navigation";

import { JobCard } from "@/components/JobCard";
import { UploadDropzone } from "@/components/UploadDropzone";
import { useJobList } from "@/lib/hooks";

export function HomeClient() {
  const router = useRouter();
  const { jobs, total, error, loading, refresh } = useJobList(12);

  return (
    <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
      <div className="space-y-6">
        <UploadDropzone
          onCreated={(jobId) => {
            void refresh();
            router.push(`/jobs/${jobId}`);
          }}
        />

        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-[var(--color-mute)]">
              Recent recordings
            </h2>
            {total > 0 && <span className="chip">{total} total</span>}
          </div>

          {error && (
            <p
              role="alert"
              className="rounded-lg border border-[#5a2226] bg-[#1a0f11] px-3 py-2 text-sm text-[#fca5a5]"
            >
              {error}
            </p>
          )}

          {loading && !jobs.length ? (
            <ul className="grid gap-3 sm:grid-cols-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <li key={i} className="card h-[104px] animate-pulse-soft" />
              ))}
            </ul>
          ) : jobs.length ? (
            <ul className="grid gap-3 sm:grid-cols-2">
              {jobs.map((job) => (
                <li key={job.id} className="animate-rise">
                  <JobCard job={job} />
                </li>
              ))}
            </ul>
          ) : (
            <div className="card p-6 text-center text-sm text-[var(--color-mute)]">
              No recordings yet. Drop a file above to run your first transcript.
            </div>
          )}
        </section>
      </div>

      <aside className="space-y-6">
        <div className="card p-5">
          <h2 className="text-sm font-semibold text-white">What M1 does</h2>
          <ol className="mt-3 space-y-2.5 text-sm text-[var(--color-mute)]">
            {[
              ["1", "Ingest", "Streaming upload with type/size validation, ffmpeg probe for real duration."],
              ["2", "Transcribe", "Word-level timestamps, 40+ languages, provider you choose."],
              ["3", "Diarize", "Speaker turns merged into lines by weighted time-overlap voting."],
              ["4", "Correct", "Click any line to fix it, reassign a speaker, rename speakers."],
              ["5", "Export", "SRT, WebVTT, TXT, Markdown and full-fidelity JSON."],
            ].map(([n, title, body]) => (
              <li key={n} className="flex gap-3">
                <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-md border border-[var(--color-line)] bg-[var(--color-ink-900)] font-mono text-[11px] text-[var(--color-accent-soft)]">
                  {n}
                </span>
                <span>
                  <span className="block text-[13px] font-medium text-white">{title}</span>
                  <span className="block text-[12px] leading-relaxed">{body}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>

        <div className="card p-5">
          <h2 className="text-sm font-semibold text-white">Coming next</h2>
          <ul className="mt-3 space-y-1.5 text-xs text-[var(--color-mute)]">
            {[
              "M2 — AI summary + key points",
              "M3 — quote-card generator (PNG/PDF)",
              "M4 — speaker rename memory, clip selection",
              "M5 — transcription memory (reuse paid ASR)",
              "M6 — auth, billing, rate limiting",
              "M7 — multilingual UI + translator",
            ].map((item) => (
              <li key={item} className="flex items-center gap-2">
                <span aria-hidden className="size-1 rounded-full bg-[var(--color-line)]" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}
