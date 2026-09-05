"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { Health, Job, JobDetail } from "@/lib/types";
import { TERMINAL_STATUSES } from "@/lib/types";

const ACTIVE_POLL_MS = 1200;
const IDLE_POLL_MS = 15_000;

interface UseJobResult {
  job: JobDetail | null;
  error: string | null;
  loading: boolean;
  /** True while the pipeline is running. */
  processing: boolean;
  refresh: () => Promise<void>;
}

/**
 * Poll one job until it reaches a terminal state.
 *
 * Polling rather than SSE/WebSocket on purpose: it is stateless, survives a
 * proxy that buffers streams (the sandbox preview does), and re-syncs the whole
 * job document — which is what the editor needs after inline edits anyway.
 */
export function useJob(jobId: string | null): UseJobResult {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(jobId));
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const fetchJob = useCallback(async () => {
    if (!jobId) return;
    try {
      const data = await api.getJob(jobId);
      if (!mounted.current) return;
      setJob(data);
      setError(null);
    } catch (err) {
      if (!mounted.current) return;
      // A 404 mid-session means the job was deleted elsewhere — surface it, but
      // do not spam the UI for a transient network blip.
      if (err instanceof ApiError && err.status === 404) {
        setError(err.detail);
      } else if (!error) {
        setError(err instanceof Error ? err.message : "Could not load this job");
      }
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [jobId, error]);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    void fetchJob();
  }, [jobId, fetchJob]);

  const processing = Boolean(job && !TERMINAL_STATUSES.includes(job.status));

  useEffect(() => {
    if (!jobId) return;
    const interval = processing ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    const timer = setInterval(() => void fetchJob(), interval);
    // Re-fetch immediately when the tab regains focus so a backgrounded tab
    // does not show a stale "processing" state.
    const onFocus = () => void fetchJob();
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [jobId, processing, fetchJob]);

  return { job, error, loading, processing, refresh: fetchJob };
}

export function useJobList(limit = 25) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api.listJobs({ limit });
      setJobs(data.items);
      setTotal(data.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load jobs");
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 8000);
    return () => clearInterval(timer);
  }, [refresh]);

  return { jobs, total, error, loading, refresh };
}

export function useHealth() {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const data = await api.health();
        if (active) setHealth(data);
      } catch {
        if (active) setHealth(null);
      }
    };
    void load();
    const timer = setInterval(load, 30_000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  return health;
}

/** Tiny debounce for autosave-on-blur style editing. */
export function useDebouncedCallback<A extends unknown[]>(
  fn: (...args: A) => void,
  delay: number,
) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saved = useRef(fn);
  saved.current = fn;

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  return useCallback(
    (...args: A) => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => saved.current(...args), delay);
    },
    [delay],
  );
}
