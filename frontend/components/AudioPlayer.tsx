"use client";

import { useEffect, useRef } from "react";

import { api } from "@/lib/api";
import { formatDuration } from "@/lib/format";

interface Props {
  jobId: string;
  filename: string;
  durationSeconds: number | null;
  /** Seek here when a transcript row's timestamp is clicked. */
  seekTo?: number | null;
  onTimeUpdate?: (seconds: number) => void;
}

/**
 * Native <audio> player wired to /api/jobs/{id}/audio.
 *
 * The browser streams it (the endpoint supports Range requests), so scrubbing a
 * two-hour recording does not download the whole file first.
 */
export function AudioPlayer({ jobId, filename, durationSeconds, seekTo, onTimeUpdate }: Props) {
  const ref = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || seekTo == null) return;
    el.currentTime = seekTo;
    void el.play().catch(() => {
      /* autoplay can be blocked before the first user gesture — not an error */
    });
  }, [seekTo]);

  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-white">{filename}</p>
          <p className="mt-0.5 text-xs text-[var(--color-mute)]">
            {durationSeconds ? formatDuration(durationSeconds) : "duration pending"} · streamed from
            the backend
          </p>
        </div>
      </div>
      <audio
        ref={ref}
        controls
        preload="metadata"
        className="w-full"
        src={api.audioUrl(jobId)}
        onTimeUpdate={(event) => onTimeUpdate?.(event.currentTarget.currentTime)}
      />
    </div>
  );
}
