/**
 * Mirrors the FastAPI schemas in backend/app/schemas.py.
 * If you change one, change the other — `GET /api/openapi.json` is the source of truth.
 */

export type JobStatus =
  | "queued"
  | "preprocessing"
  | "transcribing"
  | "diarizing"
  | "merging"
  | "aligning"
  | "completed"
  | "failed"
  | "cancelled";

export const TERMINAL_STATUSES: JobStatus[] = ["completed", "failed", "cancelled"];

export const ACTIVE_STATUSES: JobStatus[] = [
  "queued",
  "preprocessing",
  "transcribing",
  "diarizing",
  "merging",
  "aligning",
];

/** Pipeline order, used to render the stage stepper. */
export const STAGE_ORDER: JobStatus[] = [
  "queued",
  "preprocessing",
  "transcribing",
  "diarizing",
  "merging",
  "aligning",
  "completed",
];

export const STAGE_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  preprocessing: "Preparing audio",
  transcribing: "Transcribing",
  diarizing: "Diarizing",
  merging: "Assigning speakers",
  aligning: "Saving transcript",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export interface Word {
  w: string;
  s: number;
  e: number;
}

export interface Speaker {
  id: string;
  speaker_key: string;
  speaker_index: number;
  display_name: string;
  color: string | null;
  is_edited: boolean;
  word_count: number;
  segment_count: number;
}

export interface Segment {
  id: string;
  seq: number;
  start: number;
  end: number;
  text: string;
  original_text: string;
  confidence: number | null;
  is_edited: boolean;
  speaker_id: string | null;
  speaker_display_name: string | null;
  speaker_color: string | null;
  words: Word[];
}

export interface Quote {
  text: string;
  speaker?: string | null;
  start?: number;
  end?: number;
  reason?: string;
}

export interface Transcript {
  id: string;
  full_text: string | null;
  language: string | null;
  revision: number;
  summary: string | null;
  key_points: string[];
  quotes: Quote[];
  speakers: Speaker[];
  segments: Segment[];
}

export interface JobEvent {
  stage: string;
  level: string;
  message: string;
  progress: number | null;
  created_at: string;
}

export interface Job {
  id: string;
  status: JobStatus;
  progress: number;
  stage_message: string | null;
  original_filename: string;
  file_size_bytes: number;
  audio_duration_seconds: number | null;
  mime_type: string | null;
  language: string;
  detected_language: string | null;
  asr_provider: string | null;
  diarization_provider: string | null;
  attempts: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  failed_at: string | null;
}

export interface JobDetail extends Job {
  transcript: Transcript | null;
  events: JobEvent[];
}

export interface JobList {
  total: number;
  items: Job[];
}

export interface Health {
  status: "ok" | "degraded";
  app: string;
  environment: string;
  version: string;
  providers: Record<string, string>;
  providers_ready?: Record<string, boolean>;
  missing_config: string[];
  ffmpeg: string | null;
  database: string | null;
  worker: {
    mode: string;
    worker_id: string;
    max_concurrent_jobs: number;
    queue?: Record<string, number>;
    active_jobs?: number;
  };
}

export type ExportFormat = "srt" | "vtt" | "txt" | "md" | "json";

export const EXPORT_FORMATS: { id: ExportFormat; label: string; hint: string }[] = [
  { id: "srt", label: "SRT", hint: "Subtitles for video editors" },
  { id: "vtt", label: "WebVTT", hint: "Web video captions" },
  { id: "txt", label: "TXT", hint: "Speaker-timestamped plain text" },
  { id: "md", label: "Markdown", hint: "Grouped by speaker turn" },
  { id: "json", label: "JSON", hint: "Full fidelity, word timings" },
];

/** Languages offered in the upload form (backend validates the full list). */
export const LANGUAGES: { code: string; label: string }[] = [
  { code: "auto", label: "Auto-detect" },
  { code: "en", label: "English" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "pt", label: "Portuguese" },
  { code: "it", label: "Italian" },
  { code: "nl", label: "Dutch" },
  { code: "ru", label: "Russian" },
  { code: "uk", label: "Ukrainian" },
  { code: "ar", label: "Arabic" },
  { code: "he", label: "Hebrew" },
  { code: "hi", label: "Hindi" },
  { code: "bn", label: "Bengali" },
  { code: "ta", label: "Tamil" },
  { code: "te", label: "Telugu" },
  { code: "mr", label: "Marathi" },
  { code: "gu", label: "Gujarati" },
  { code: "kn", label: "Kannada" },
  { code: "ml", label: "Malayalam" },
  { code: "pa", label: "Punjabi" },
  { code: "ur", label: "Urdu" },
  { code: "id", label: "Indonesian" },
  { code: "ms", label: "Malay" },
  { code: "th", label: "Thai" },
  { code: "vi", label: "Vietnamese" },
  { code: "tl", label: "Filipino" },
  { code: "zh", label: "Chinese" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
  { code: "tr", label: "Turkish" },
  { code: "pl", label: "Polish" },
];
