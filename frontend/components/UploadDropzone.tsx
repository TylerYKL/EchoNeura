"use client";

import { useCallback, useId, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { LANGUAGES } from "@/lib/types";

const ACCEPT =
  ".mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.webm,.mp4,.mov,.wma,.aiff,audio/*,video/*";

interface Props {
  onCreated: (jobId: string) => void;
}

type Phase = "idle" | "uploading" | "error";

/**
 * Drag-and-drop upload with client-side validation.
 *
 * Validation here is a courtesy: the backend re-validates type, size and
 * language, because a browser check cannot be trusted.
 */
export function UploadDropzone({ onCreated }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState("auto");
  const [wordTimestamps, setWordTimestamps] = useState(true);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState(0);
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  const pick = useCallback((incoming: File | null | undefined) => {
    setError(null);
    if (!incoming) return;
    const isAudioish =
      incoming.type.startsWith("audio/") ||
      incoming.type.startsWith("video/") ||
      /\.(mp3|wav|m4a|aac|flac|ogg|oga|opus|webm|mp4|mov|wma|aiff?)$/i.test(incoming.name);
    if (!isAudioish) {
      setPhase("error");
      setError(
        `“${incoming.name}” does not look like audio or video. Send an .mp3, .wav, .m4a, .flac, .ogg, .mp4 or .mov.`,
      );
      return;
    }
    if (incoming.size === 0) {
      setPhase("error");
      setError("That file is empty.");
      return;
    }
    setFile(incoming);
    setPhase("idle");
    setProgress(0);
  }, []);

  const submit = useCallback(async () => {
    if (!file) return;
    setPhase("uploading");
    setError(null);
    setProgress(0);

    // XMLHttpRequest instead of fetch: fetch cannot report upload progress, and
    // a silent 400 MB upload looks exactly like a hung app.
    try {
      const jobId = await new Promise<string>((resolve, reject) => {
        const form = new FormData();
        form.append("file", file);
        form.append("language", language);
        form.append("word_timestamps", String(wordTimestamps));

        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/jobs");
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            setProgress(Math.round((event.loaded / event.total) * 100));
          }
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              resolve(JSON.parse(xhr.responseText).id as string);
            } catch {
              reject(new ApiError(xhr.status, "Malformed response from the API"));
            }
          } else {
            let detail = `Upload failed (${xhr.status})`;
            try {
              const body = JSON.parse(xhr.responseText);
              if (typeof body?.detail === "string") detail = body.detail;
            } catch {
              /* keep the generic message */
            }
            reject(new ApiError(xhr.status, detail));
          }
        };
        xhr.onerror = () => reject(new ApiError(0, "Network error while uploading"));
        xhr.send(form);
      });
      onCreated(jobId);
    } catch (err) {
      setPhase("error");
      setError(err instanceof Error ? err.message : "Upload failed");
    }
  }, [file, language, onCreated, wordTimestamps]);

  const busy = phase === "uploading";

  return (
    <section className="card p-5 sm:p-6">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-white">New recording</h2>
          <p className="mt-1 text-sm text-[var(--color-mute)]">
            Drop an audio or video file. It is queued and processed in the background.
          </p>
        </div>
        <span className="chip shrink-0">max 500 MB</span>
      </div>

      <label
        htmlFor={inputId}
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (busy) return;
          pick(event.dataTransfer.files?.[0]);
        }}
        className={[
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-6 py-10 text-center transition",
          dragging
            ? "border-[var(--color-accent)] bg-[#141733]"
            : "border-[var(--color-line)] bg-[var(--color-ink-900)] hover:border-[#3a4260] hover:bg-[var(--color-ink-850)]",
          busy ? "pointer-events-none opacity-60" : "",
        ].join(" ")}
      >
        <span aria-hidden className="text-2xl">
          {file ? "🎙️" : "⬆️"}
        </span>
        <span className="text-sm font-medium text-white">
          {file ? file.name : "Drag audio here, or click to browse"}
        </span>
        <span className="text-xs text-[var(--color-mute)]">
          {file
            ? `${formatBytes(file.size)} · ready to process`
            : "MP3 · WAV · M4A · FLAC · OGG · MP4 · MOV"}
        </span>
        <input
          id={inputId}
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          disabled={busy}
          onChange={(event) => {
            pick(event.target.files?.[0]);
            event.target.value = ""; // allow re-selecting the same file
          }}
        />
      </label>

      <div className="mt-4 grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-end">
        <div>
          <label className="label" htmlFor={`${inputId}-lang`}>
            Source language
          </label>
          <select
            id={`${inputId}-lang`}
            className="input"
            value={language}
            disabled={busy}
            onChange={(event) => setLanguage(event.target.value)}
          >
            {LANGUAGES.map((option) => (
              <option key={option.code} value={option.code}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <label className="flex cursor-pointer items-center gap-2 pb-2 text-sm text-[var(--color-mute)]">
          <input
            type="checkbox"
            className="size-4 accent-[var(--color-accent)]"
            checked={wordTimestamps}
            disabled={busy}
            onChange={(event) => setWordTimestamps(event.target.checked)}
          />
          Word timestamps
        </label>

        <button
          type="button"
          className="btn btn-primary h-[38px] disabled:cursor-not-allowed"
          disabled={!file || busy}
          onClick={() => void submit()}
        >
          {busy ? `Uploading ${progress}%` : "Transcribe"}
        </button>
      </div>

      {busy && (
        <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-[var(--color-ink-800)]">
          <div
            className="h-full rounded-full bg-[var(--color-accent)] transition-[width] duration-200"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}

      {error && (
        <p
          role="alert"
          className="mt-4 rounded-lg border border-[#5a2226] bg-[#1a0f11] px-3 py-2 text-sm text-[#fca5a5]"
        >
          {error}
        </p>
      )}
    </section>
  );
}
