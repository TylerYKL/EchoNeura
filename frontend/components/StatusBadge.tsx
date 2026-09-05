"use client";

import type { JobStatus } from "@/lib/types";
import { STAGE_LABELS } from "@/lib/types";

const TONES: Record<JobStatus, string> = {
  queued: "bg-[#1b2030] text-[#a7b0c6] border-[#2b3346]",
  preprocessing: "bg-[#16232f] text-[#7cc4e8] border-[#1f3d52]",
  transcribing: "bg-[#1b1f3a] text-[#a5b4fc] border-[#2e3563]",
  diarizing: "bg-[#1d1a33] text-[#c4b5fd] border-[#372c5e]",
  merging: "bg-[#12251f] text-[#6ee7b7] border-[#1d4436]",
  aligning: "bg-[#241f13] text-[#fcd34d] border-[#4a3d1c]",
  completed: "bg-[#12251c] text-[#6ee7b7] border-[#1d4436]",
  failed: "bg-[#2a1416] text-[#fca5a5] border-[#5a2226]",
  cancelled: "bg-[#211d14] text-[#d6c08a] border-[#463a1e]",
};

const ACTIVE: JobStatus[] = [
  "queued",
  "preprocessing",
  "transcribing",
  "diarizing",
  "merging",
  "aligning",
];

export function StatusBadge({ status }: { status: JobStatus }) {
  const active = ACTIVE.includes(status);
  return (
    <span
      className={`chip border ${TONES[status] ?? TONES.queued} ${active ? "animate-pulse-soft" : ""}`}
    >
      {active && (
        <span aria-hidden className="size-1.5 rounded-full bg-current" />
      )}
      {STAGE_LABELS[status] ?? status}
    </span>
  );
}
