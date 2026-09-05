"use client";

import type { JobDetail, JobStatus } from "@/lib/types";
import { STAGE_LABELS, STAGE_ORDER } from "@/lib/types";

const INDEX: Record<string, number> = Object.fromEntries(
  STAGE_ORDER.map((stage, i) => [stage, i]),
);

/**
 * Stage stepper + progress bar.
 *
 * Shows *where* the pipeline is, not just a percentage — "Transcribing" tells a
 * user something is happening, "43%" does not.
 */
export function JobProgress({ job }: { job: JobDetail }) {
  const failed = job.status === "failed";
  const cancelled = job.status === "cancelled";
  const currentIndex = failed || cancelled ? INDEX.transcribing : (INDEX[job.status] ?? 0);
  const pct = Math.max(0, Math.min(100, job.progress));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-[var(--color-mute)]">
          {failed ? (
            <span className="text-[#fca5a5]">Processing failed</span>
          ) : cancelled ? (
            <span className="text-[#d6c08a]">Cancelled</span>
          ) : (
            job.stage_message || STAGE_LABELS[job.status as JobStatus] || "Working…"
          )}
        </p>
        <span className="font-mono text-sm text-white tabular-nums">{pct}%</span>
      </div>

      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-ink-800)]"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Processing progress"
      >
        <div
          className={`h-full rounded-full transition-[width] duration-500 ease-out ${
            failed
              ? "bg-[#ef4444]"
              : cancelled
                ? "bg-[#a3863f]"
                : "bg-gradient-to-r from-[var(--color-accent)] to-[#22d3ee]"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <ol className="flex flex-wrap gap-x-1 gap-y-2 text-[11px]">
        {STAGE_ORDER.map((stage, i) => {
          const done = i < currentIndex || job.status === "completed";
          const current = i === currentIndex && job.status !== "completed";
          return (
            <li key={stage} className="flex items-center gap-1">
              <span
                className={[
                  "inline-flex items-center gap-1.5 rounded-md border px-2 py-1",
                  done
                    ? "border-[#1d4436] bg-[#12251c] text-[#6ee7b7]"
                    : current
                      ? "border-[#2e3563] bg-[#1b1f3a] text-[#a5b4fc] animate-pulse-soft"
                      : "border-[var(--color-line-soft)] bg-[var(--color-ink-900)] text-[#5f6779]",
                ].join(" ")}
              >
                <span
                  aria-hidden
                  className={`size-1.5 rounded-full ${done ? "bg-[#6ee7b7]" : current ? "bg-[#a5b4fc]" : "bg-[#3a4256]"}`}
                />
                {STAGE_LABELS[stage]}
              </span>
              {i < STAGE_ORDER.length - 1 && (
                <span aria-hidden className="text-[#3a4256]">
                  ›
                </span>
              )}
            </li>
          );
        })}
      </ol>

      {failed && job.error && (
        <pre className="max-h-40 overflow-auto rounded-lg border border-[#5a2226] bg-[#1a0f11] p-3 font-mono text-[11px] leading-relaxed text-[#fca5a5] whitespace-pre-wrap">
          {job.error}
        </pre>
      )}
    </div>
  );
}
