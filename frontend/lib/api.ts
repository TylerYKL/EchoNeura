/**
 * API client.
 *
 * All URLs are relative (`/api/...`) and proxied to FastAPI by next.config.mjs.
 * Never hardcode a backend host here: the browser cannot reach the sandbox's
 * 127.0.0.1, and a relative path keeps dev/preview/production identical.
 */

import type { ExportFormat, Health, Job, JobDetail, JobList, Segment, Speaker } from "./types";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function parseError(response: Response): Promise<never> {
  let detail = `${response.status} ${response.statusText}`;
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") {
      detail = body.detail;
    } else if (Array.isArray(body?.detail)) {
      // FastAPI validation errors.
      detail = body.detail
        .map((d: { msg?: string; loc?: (string | number)[] }) =>
          d.loc?.length ? `${d.loc.slice(1).join(".")}: ${d.msg}` : (d.msg ?? "invalid"),
        )
        .join("; ");
    }
  } catch {
    /* non-JSON error body; keep the status text */
  }
  throw new ApiError(response.status, detail);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      cache: "no-store",
      ...init,
      headers: {
        ...(init?.body && !(init.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        ...init?.headers,
      },
    });
  } catch (error) {
    throw new ApiError(
      0,
      error instanceof Error
        ? `Cannot reach the API: ${error.message}`
        : "Cannot reach the API",
    );
  }
  if (!response.ok) await parseError(response);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),

  listJobs: (params?: { status?: string; limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params?.status) query.set("status", params.status);
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.offset) query.set("offset", String(params.offset));
    const qs = query.toString();
    return request<JobList>(`/api/jobs${qs ? `?${qs}` : ""}`);
  },

  getJob: (id: string) => request<JobDetail>(`/api/jobs/${id}`),

  createJob: (file: File, options: { language: string; wordTimestamps: boolean }) => {
    const form = new FormData();
    form.append("file", file);
    form.append("language", options.language);
    form.append("word_timestamps", String(options.wordTimestamps));
    return request<Job>("/api/jobs", { method: "POST", body: form });
  },

  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  reprocessJob: (id: string) => request<Job>(`/api/jobs/${id}/reprocess`, { method: "POST" }),
  deleteJob: (id: string) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),

  renameSpeaker: (jobId: string, speakerId: string, displayName: string) =>
    request<Speaker>(`/api/jobs/${jobId}/speakers/${speakerId}`, {
      method: "PATCH",
      body: JSON.stringify({ display_name: displayName }),
    }),

  recolorSpeaker: (jobId: string, speakerId: string, color: string) =>
    request<Speaker>(`/api/jobs/${jobId}/speakers/${speakerId}/color?color=${encodeURIComponent(color)}`, {
      method: "PATCH",
    }),

  updateSegment: (
    jobId: string,
    segmentId: string,
    patch: { text?: string; speaker_id?: string; start?: number; end?: number },
  ) =>
    request<Segment>(`/api/jobs/${jobId}/segments/${segmentId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  updateSegmentsBulk: (
    jobId: string,
    updates: { id: string; text?: string; speaker_id?: string; start?: number; end?: number }[],
  ) =>
    request<Segment[]>(`/api/jobs/${jobId}/segments`, {
      method: "PATCH",
      body: JSON.stringify({ updates }),
    }),

  resetEdits: (jobId: string) => request<JobDetail>(`/api/jobs/${jobId}/segments/reset`, {
    method: "POST",
  }),

  transcriptText: async (jobId: string) => {
    const response = await fetch(`/api/jobs/${jobId}/transcript.txt`, { cache: "no-store" });
    if (!response.ok) await parseError(response);
    return response.text();
  },

  /** Export URLs are used directly as hrefs so the browser handles the download. */
  exportUrl: (jobId: string, format: ExportFormat, options?: Record<string, string | number | boolean>) => {
    const query = new URLSearchParams();
    Object.entries(options ?? {}).forEach(([key, value]) => query.set(key, String(value)));
    const qs = query.toString();
    return `/api/jobs/${jobId}/export/${format}${qs ? `?${qs}` : ""}`;
  },

  audioUrl: (jobId: string) => `/api/jobs/${jobId}/audio`,
};
