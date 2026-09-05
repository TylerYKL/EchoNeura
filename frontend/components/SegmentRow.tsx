"use client";

import { useEffect, useRef, useState } from "react";

import { confidenceTone, formatClock, formatSrtClock } from "@/lib/format";
import type { Segment, Speaker } from "@/lib/types";

interface Props {
  segment: Segment;
  speakers: Speaker[];
  active: boolean;
  saving: boolean;
  onSeek: (seconds: number) => void;
  onSaveText: (segmentId: string, text: string) => void;
  onSaveSpeaker: (segmentId: string, speakerId: string) => void;
  onRevert: (segmentId: string) => void;
}

const TONE_CLASS = {
  good: "text-[#6ee7b7]",
  warn: "text-[#fcd34d]",
  bad: "text-[#fca5a5]",
} as const;

/**
 * One editable transcript line.
 *
 * Editing model: the textarea holds a local draft and commits on blur or
 * Ctrl/Cmd+Enter. Committing on every keystroke would fire a PATCH per character
 * and fight the poller for the same field; committing only on blur makes the
 * "edited" state meaningful.
 */
export function SegmentRow({
  segment,
  speakers,
  active,
  saving,
  onSeek,
  onSaveText,
  onSaveSpeaker,
  onRevert,
}: Props) {
  const [draft, setDraft] = useState(segment.text);
  const [editing, setEditing] = useState(false);
  const [showTimes, setShowTimes] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  // Adopt server text unless the user is mid-edit (never clobber a draft).
  useEffect(() => {
    if (!editing) setDraft(segment.text);
  }, [segment.text, editing]);

  useEffect(() => {
    if (editing && ref.current) {
      ref.current.style.height = "auto";
      ref.current.style.height = `${ref.current.scrollHeight}px`;
    }
  }, [editing, draft]);

  const commit = () => {
    setEditing(false);
    const next = draft.trim();
    if (next !== segment.text) onSaveText(segment.id, next);
    else setDraft(segment.text);
  };

  const dirty = editing && draft.trim() !== segment.text;

  return (
    <li
      className={[
        "group relative rounded-lg border px-3 py-2.5 transition",
        active
          ? "border-[#3a4260] bg-[var(--color-ink-850)]"
          : "border-transparent hover:border-[var(--color-line-soft)] hover:bg-[var(--color-ink-900)]",
        segment.is_edited ? "border-l-2" : "",
      ].join(" ")}
      style={segment.is_edited ? { borderLeftColor: segment.speaker_color ?? "#6366f1" } : undefined}
    >
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={() => onSeek(segment.start)}
          title="Play from here"
          className="mt-0.5 shrink-0 rounded-md border border-[var(--color-line-soft)] bg-[var(--color-ink-900)] px-1.5 py-1 font-mono text-[11px] text-[var(--color-mute)] transition hover:border-[var(--color-accent)] hover:text-white"
        >
          {showTimes ? formatSrtClock(segment.start) : formatClock(segment.start)}
        </button>

        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <select
              value={segment.speaker_id ?? ""}
              aria-label="Speaker for this line"
              onChange={(event) => onSaveSpeaker(segment.id, event.target.value)}
              className="max-w-[160px] truncate rounded-md border border-[var(--color-line-soft)] bg-[var(--color-ink-900)] px-1.5 py-0.5 text-[11px] font-medium text-white hover:border-[#3a4260] focus:border-[var(--color-accent)] focus:outline-none"
              style={{ borderLeftColor: segment.speaker_color ?? undefined, borderLeftWidth: 3 }}
            >
              <option value="" disabled>
                Unassigned
              </option>
              {speakers.map((speaker) => (
                <option key={speaker.id} value={speaker.id}>
                  {speaker.display_name}
                </option>
              ))}
            </select>

            {segment.confidence != null && (
              <span
                className={`font-mono text-[10px] ${TONE_CLASS[confidenceTone(segment.confidence)]}`}
                title="Mean model confidence for this line"
              >
                {(segment.confidence * 100).toFixed(0)}%
              </span>
            )}

            {segment.is_edited && (
              <span className="chip border-[#1d4436] bg-[#12251c] text-[10px] text-[#6ee7b7]">
                edited
              </span>
            )}

            {segment.words.length > 0 && (
              <button
                type="button"
                onClick={() => setShowTimes((v) => !v)}
                className="btn btn-ghost btn-sm px-1.5 py-0 text-[10px] opacity-0 transition group-hover:opacity-100 focus:opacity-100"
                title="Toggle precise timestamps"
              >
                ms
              </button>
            )}

            {segment.is_edited && (
              <button
                type="button"
                onClick={() => onRevert(segment.id)}
                className="btn btn-ghost btn-sm px-1.5 py-0 text-[10px] opacity-0 transition group-hover:opacity-100 focus:opacity-100"
                title="Revert to model output"
              >
                revert
              </button>
            )}
          </div>

          {editing ? (
            <textarea
              ref={ref}
              value={draft}
              rows={1}
              onChange={(event) => setDraft(event.target.value)}
              onBlur={commit}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  commit();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  setDraft(segment.text);
                  setEditing(false);
                }
              }}
              className="w-full resize-none rounded-md border border-[var(--color-accent)] bg-[var(--color-ink-950)] px-2 py-1.5 text-sm leading-relaxed text-white focus:outline-none"
              autoFocus
            />
          ) : (
            <p
              onClick={() => setEditing(true)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setEditing(true);
                }
              }}
              role="button"
              tabIndex={0}
              title="Click to correct this line"
              className="cursor-text rounded-md px-2 py-1.5 text-sm leading-relaxed text-[#dfe3ee] transition hover:bg-[var(--color-ink-850)]"
            >
              {segment.text || <span className="text-[var(--color-mute)]">(empty)</span>}
            </p>
          )}

          {dirty && (
            <p className="mt-1 px-2 text-[10px] text-[var(--color-mute)]">
              ⌘/Ctrl + Enter to save · Esc to discard
            </p>
          )}
        </div>

        {saving && (
          <span className="mt-1 shrink-0 text-[10px] text-[var(--color-mute)] animate-pulse-soft">
            saving…
          </span>
        )}
      </div>
    </li>
  );
}
