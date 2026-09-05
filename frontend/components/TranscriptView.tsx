"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SegmentRow } from "@/components/SegmentRow";
import { api } from "@/lib/api";
import type { Segment, Speaker } from "@/lib/types";

interface Props {
  jobId: string;
  segments: Segment[];
  speakers: Speaker[];
  currentTime: number;
  onSeek: (seconds: number) => void;
  onChanged: () => void;
}

interface PendingEdit {
  text?: string;
  speaker_id?: string;
}

const AUTO_RENDER_LIMIT = 400;

/**
 * Transcript editor.
 *
 * Edits are batched: each change lands in a pending map and is flushed as one
 * bulk PATCH (debounced 700 ms, and immediately on unmount/visibility change).
 * That keeps a fast typist from generating one HTTP request per keystroke while
 * still never losing work if they close the tab.
 */
export function TranscriptView({
  jobId,
  segments,
  speakers,
  currentTime,
  onSeek,
  onChanged,
}: Props) {
  const [query, setQuery] = useState("");
  const [onlyEdited, setOnlyEdited] = useState(false);
  const [onlySpeaker, setOnlySpeaker] = useState<string>("");
  const [showAll, setShowAll] = useState(false);
  const [pending, setPending] = useState<Record<string, PendingEdit>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(async () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const entries = Object.entries(pendingRef.current);
    if (!entries.length) return;

    setPending({});
    setSaving(true);
    setError(null);
    try {
      await api.updateSegmentsBulk(
        jobId,
        entries.map(([id, edit]) => ({ id, ...edit })),
      );
      onChanged();
    } catch (err) {
      // Put the edits back so nothing is silently lost, then tell the user.
      setPending((current) => {
        const restored = { ...current };
        for (const [id, edit] of entries) restored[id] = { ...restored[id], ...edit };
        return restored;
      });
      setError(err instanceof Error ? err.message : "Could not save your edits");
    } finally {
      setSaving(false);
    }
  }, [jobId, onChanged]);

  const schedule = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), 700);
  }, [flush]);

  const queue = useCallback(
    (segmentId: string, edit: PendingEdit) => {
      setPending((current) => ({
        ...current,
        [segmentId]: { ...current[segmentId], ...edit },
      }));
      schedule();
    },
    [schedule],
  );

  // Never drop unsaved edits on navigation or tab close.
  useEffect(() => () => void flush(), [flush]);
  useEffect(() => {
    const onHidden = () => {
      if (document.visibilityState === "hidden") void flush();
    };
    document.addEventListener("visibilitychange", onHidden);
    window.addEventListener("pagehide", onHidden);
    return () => {
      document.removeEventListener("visibilitychange", onHidden);
      window.removeEventListener("pagehide", onHidden);
    };
  }, [flush]);

  const revert = useCallback(
    (segmentId: string) => {
      const segment = segments.find((s) => s.id === segmentId);
      if (!segment) return;
      queue(segmentId, { text: segment.original_text });
    },
    [queue, segments],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return segments.filter((segment) => {
      if (onlyEdited && !segment.is_edited) return false;
      if (onlySpeaker && segment.speaker_id !== onlySpeaker) return false;
      if (!needle) return true;
      return (
        segment.text.toLowerCase().includes(needle) ||
        (segment.speaker_display_name ?? "").toLowerCase().includes(needle)
      );
    });
  }, [onlyEdited, onlySpeaker, query, segments]);

  const visible = showAll ? filtered : filtered.slice(0, AUTO_RENDER_LIMIT);
  const pendingCount = Object.keys(pending).length;
  const editedCount = segments.filter((s) => s.is_edited).length;

  const activeId = useMemo(() => {
    let best: string | null = null;
    for (const segment of segments) {
      if (segment.start <= currentTime) best = segment.id;
      else break;
    }
    return best;
  }, [currentTime, segments]);

  return (
    <div className="card flex min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-[var(--color-line-soft)] p-3">
        <h3 className="mr-auto text-sm font-semibold text-white">
          Transcript
          <span className="ml-2 text-xs font-normal text-[var(--color-mute)]">
            {filtered.length === segments.length
              ? `${segments.length} lines`
              : `${filtered.length} of ${segments.length} lines`}
          </span>
        </h3>

        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search transcript…"
          aria-label="Search transcript"
          className="input h-8 w-full max-w-[220px] text-xs"
        />

        <select
          value={onlySpeaker}
          onChange={(event) => setOnlySpeaker(event.target.value)}
          aria-label="Filter by speaker"
          className="input h-8 w-auto max-w-[150px] text-xs"
        >
          <option value="">All speakers</option>
          {speakers.map((speaker) => (
            <option key={speaker.id} value={speaker.id}>
              {speaker.display_name}
            </option>
          ))}
        </select>

        <button
          type="button"
          onClick={() => setOnlyEdited((v) => !v)}
          className={[
            "btn btn-sm h-8",
            onlyEdited ? "border-[var(--color-accent)] bg-[#141733] text-white" : "",
          ].join(" ")}
        >
          Edited {editedCount > 0 && <span className="font-mono opacity-70">{editedCount}</span>}
        </button>

        <span className="chip h-8" aria-live="polite">
          {saving ? "saving…" : pendingCount ? `${pendingCount} unsaved` : "all changes saved"}
        </span>
      </header>

      {error && (
        <p
          role="alert"
          className="flex items-center justify-between gap-3 border-b border-[#5a2226] bg-[#1a0f11] px-3 py-2 text-xs text-[#fca5a5]"
        >
          {error}
          <button type="button" className="btn btn-sm" onClick={() => void flush()}>
            Retry save
          </button>
        </p>
      )}

      <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2">
        {visible.map((segment) => (
          <SegmentRow
            key={segment.id}
            segment={
              pending[segment.id]
                ? {
                    ...segment,
                    text: pending[segment.id].text ?? segment.text,
                    speaker_id: pending[segment.id].speaker_id ?? segment.speaker_id,
                  }
                : segment
            }
            speakers={speakers}
            active={segment.id === activeId}
            saving={saving && Boolean(pending[segment.id])}
            onSeek={onSeek}
            onSaveText={(id, text) => queue(id, { text })}
            onSaveSpeaker={(id, speakerId) => {
              if (speakerId) queue(id, { speaker_id: speakerId });
            }}
            onRevert={revert}
          />
        ))}

        {!visible.length && (
          <li className="px-3 py-10 text-center text-sm text-[var(--color-mute)]">
            {segments.length ? "No lines match those filters." : "Transcript is empty."}
          </li>
        )}
      </ul>

      {filtered.length > AUTO_RENDER_LIMIT && !showAll && (
        <footer className="border-t border-[var(--color-line-soft)] p-2 text-center">
          <button type="button" className="btn btn-sm" onClick={() => setShowAll(true)}>
            Show all {filtered.length} lines
          </button>
          <p className="mt-1.5 text-[11px] text-[var(--color-mute)]">
            Rendering the first {AUTO_RENDER_LIMIT} keeps long recordings responsive.
          </p>
        </footer>
      )}
    </div>
  );
}
