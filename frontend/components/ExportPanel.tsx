"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { EXPORT_FORMATS, type ExportFormat } from "@/lib/types";

interface Props {
  jobId: string;
  disabled?: boolean;
}

/**
 * Export panel.
 *
 * Downloads are plain links to the API (the browser handles the attachment), so
 * large transcripts never get buffered through JS memory.
 */
export function ExportPanel({ jobId, disabled }: Props) {
  const [format, setFormat] = useState<ExportFormat>("srt");
  const [speakerNames, setSpeakerNames] = useState(true);
  const [timestamps, setTimestamps] = useState(true);
  const [lineWidth, setLineWidth] = useState(42);

  const options: Record<string, string | number | boolean> = {
    include_speaker_names: speakerNames,
    include_timestamps: timestamps,
  };
  if (format === "srt" || format === "vtt") {
    options.max_chars_per_line = lineWidth;
    options.max_lines_per_cue = 2;
  }

  const href = api.exportUrl(jobId, format, options);

  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white">Export</h3>
        {disabled && <span className="chip border-[#463a1e] bg-[#211d14] text-[#d6c08a]">not ready</span>}
      </div>

      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
        {EXPORT_FORMATS.map((option) => (
          <button
            key={option.id}
            type="button"
            title={option.hint}
            onClick={() => setFormat(option.id)}
            className={[
              "rounded-lg border px-2 py-1.5 text-left text-xs transition",
              format === option.id
                ? "border-[var(--color-accent)] bg-[#141733] text-white"
                : "border-[var(--color-line-soft)] bg-[var(--color-ink-900)] text-[var(--color-mute)] hover:border-[#3a4260] hover:text-white",
            ].join(" ")}
          >
            <span className="block font-semibold">{option.label}</span>
            <span className="mt-0.5 block text-[10px] leading-tight opacity-75">{option.hint}</span>
          </button>
        ))}
      </div>

      <div className="mt-3 space-y-2 border-t border-[var(--color-line-soft)] pt-3 text-xs text-[var(--color-mute)]">
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            className="size-3.5 accent-[var(--color-accent)]"
            checked={speakerNames}
            onChange={(event) => setSpeakerNames(event.target.checked)}
          />
          Prefix speaker names
        </label>
        {format !== "srt" && format !== "vtt" && (
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              className="size-3.5 accent-[var(--color-accent)]"
              checked={timestamps}
              onChange={(event) => setTimestamps(event.target.checked)}
            />
            Include timestamps
          </label>
        )}
        {(format === "srt" || format === "vtt") && (
          <label className="flex items-center gap-2">
            <span className="shrink-0">Line width</span>
            <input
              type="range"
              min={20}
              max={80}
              step={1}
              value={lineWidth}
              onChange={(event) => setLineWidth(Number(event.target.value))}
              className="w-full accent-[var(--color-accent)]"
            />
            <span className="w-8 shrink-0 text-right font-mono text-white">{lineWidth}</span>
          </label>
        )}
      </div>

      <a
        href={href}
        download
        aria-disabled={disabled}
        className={`btn btn-primary mt-3 w-full ${disabled ? "pointer-events-none opacity-40" : ""}`}
      >
        Download .{format}
      </a>
    </div>
  );
}
