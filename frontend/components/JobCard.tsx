"use client";

import Link from "next/link";

import { StatusBadge } from "@/components/StatusBadge";
import { formatBytes, formatDuration, formatRelative } from "@/lib/format";
import type { Job } from "@/lib/types";

export function JobCard({ job }: { job: Job }) {
  const active = job.status !== "completed" && job.status !== "failed" && job.status !== "cancelled";

  return (
    <Link
      href={`/jobs/${job.id}`}
      className="card group block p-4 transition hover:border-[#3a4260] hover:bg-[var(--color-ink-850)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-white group-hover:text-[var(--color-accent-soft)]">
            {job.original_filename}
          </p>
          <p className="mt-1 text-xs text-[var(--color-mute)]">
            {formatRelative(job.created_at)} · {formatBytes(job.file_size_bytes)}
            {job.audio_duration_seconds ? ` · ${formatDuration(job.audio_duration_seconds)}` : ""}
            {job.asr_provider ? ` · ${job.asr_provider}` : ""}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>

      <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-[var(--color-ink-800)]">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${
            job.status === "failed"
              ? "bg-[#ef4444]"
              : job.status === "completed"
                ? "bg-[#10b981]"
                : "bg-[var(--color-accent)]"
          }`}
          style={{ width: `${Math.max(2, Math.min(100, job.progress))}%` }}
        />
      </div>

      {active && job.stage_message && (
        <p className="mt-2 truncate text-[11px] text-[var(--color-mute)]">{job.stage_message}</p>
      )}
      {job.status === "failed" && job.error && (
        <p className="mt-2 line-clamp-2 text-[11px] text-[#fca5a5]">{job.error}</p>
      )}
    </Link>
  );
}
