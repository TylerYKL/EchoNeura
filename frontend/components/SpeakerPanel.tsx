"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { Speaker } from "@/lib/types";

const SWATCHES = [
  "#6366f1",
  "#0ea5e9",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#a855f7",
  "#14b8a6",
  "#f97316",
];

interface Props {
  jobId: string;
  speakers: Speaker[];
  onChanged: () => void;
}

/**
 * Speaker list with inline rename (M1 requirement).
 *
 * Rename on blur/Enter rather than per keystroke: one PATCH per intentional edit
 * keeps the audit log readable and avoids a request per character.
 */
export function SpeakerPanel({ jobId, speakers, onChanged }: Props) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDrafts(Object.fromEntries(speakers.map((s) => [s.id, s.display_name])));
  }, [speakers]);

  const commit = async (speaker: Speaker) => {
    const next = (drafts[speaker.id] ?? "").trim();
    setError(null);
    if (!next || next === speaker.display_name) {
      setDrafts((d) => ({ ...d, [speaker.id]: speaker.display_name }));
      return;
    }
    setSaving(speaker.id);
    try {
      await api.renameSpeaker(jobId, speaker.id, next);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not rename speaker");
      setDrafts((d) => ({ ...d, [speaker.id]: speaker.display_name }));
    } finally {
      setSaving(null);
    }
  };

  const recolor = async (speaker: Speaker, color: string) => {
    setError(null);
    try {
      await api.recolorSpeaker(jobId, speaker.id, color);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update colour");
    }
  };

  if (!speakers.length) {
    return (
      <div className="card p-4 text-sm text-[var(--color-mute)]">
        No speakers yet — diarization runs after transcription.
      </div>
    );
  }

  const totalWords = speakers.reduce((sum, s) => sum + s.word_count, 0) || 1;

  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white">Speakers</h3>
        <span className="chip">{speakers.length} detected</span>
      </div>

      <ul className="space-y-2">
        {speakers.map((speaker) => {
          const share = Math.round((speaker.word_count / totalWords) * 100);
          return (
            <li key={speaker.id} className="rounded-lg border border-[var(--color-line-soft)] bg-[var(--color-ink-900)] p-2.5">
              <div className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: speaker.color ?? "#6366f1" }}
                />
                <input
                  className="min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1.5 py-1 text-sm font-medium text-white hover:border-[var(--color-line)] focus:border-[var(--color-accent)] focus:bg-[var(--color-ink-850)] focus:outline-none"
                  value={drafts[speaker.id] ?? speaker.display_name}
                  disabled={saving === speaker.id}
                  aria-label={`Display name for ${speaker.speaker_key}`}
                  onChange={(event) =>
                    setDrafts((d) => ({ ...d, [speaker.id]: event.target.value }))
                  }
                  onBlur={() => void commit(speaker)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      (event.target as HTMLInputElement).blur();
                    } else if (event.key === "Escape") {
                      setDrafts((d) => ({ ...d, [speaker.id]: speaker.display_name }));
                      (event.target as HTMLInputElement).blur();
                    }
                  }}
                />
                {speaker.is_edited && (
                  <span className="chip shrink-0 border-[#1d4436] bg-[#12251c] text-[#6ee7b7]">
                    renamed
                  </span>
                )}
              </div>

              <div className="mt-2 flex items-center gap-2 text-[11px] text-[var(--color-mute)]">
                <span className="font-mono">{speaker.speaker_key}</span>
                <span aria-hidden>·</span>
                <span>
                  {speaker.segment_count} line{speaker.segment_count === 1 ? "" : "s"}
                </span>
                <span aria-hidden>·</span>
                <span>{speaker.word_count} words ({share}%)</span>
              </div>

              <div className="mt-2 flex items-center gap-1.5">
                {SWATCHES.map((color) => (
                  <button
                    key={color}
                    type="button"
                    title={`Use ${color}`}
                    aria-label={`Set ${speaker.display_name} colour to ${color}`}
                    onClick={() => void recolor(speaker, color)}
                    className={[
                      "size-4 rounded-full border transition",
                      speaker.color === color
                        ? "border-white/80 scale-110"
                        : "border-transparent opacity-70 hover:opacity-100",
                    ].join(" ")}
                    style={{ backgroundColor: color }}
                  />
                ))}
              </div>
            </li>
          );
        })}
      </ul>

      {error && (
        <p role="alert" className="mt-3 rounded-md border border-[#5a2226] bg-[#1a0f11] px-2.5 py-1.5 text-xs text-[#fca5a5]">
          {error}
        </p>
      )}
    </div>
  );
}
