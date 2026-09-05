import { HomeClient } from "@/components/HomeClient";
import type { Health } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * Server-side health probe.
 *
 * This runs in the Next server (which CAN reach the backend directly), so a
 * misconfigured deployment is visible on first paint instead of only after a
 * failed upload.
 */
async function getHealth(): Promise<Health | null> {
  const base = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${base}/api/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(2500),
    });
    if (!response.ok) return null;
    return (await response.json()) as Health;
  } catch {
    return null;
  }
}

export default async function HomePage() {
  const health = await getHealth();

  return (
    <>
      <section className="mt-10 max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          Turn recordings into <span className="text-[var(--color-accent-soft)]">publishable</span>{" "}
          content.
        </h1>
        <p className="mt-3 text-[15px] leading-relaxed text-[var(--color-mute)]">
          Upload an interview, lecture or podcast episode. EchoNeura transcribes it with
          speaker separation, lets you correct the result inline, and exports clean subtitles and
          transcripts.
        </p>
      </section>

      <HealthBanner health={health} />
      <HomeClient />
    </>
  );
}

function HealthBanner({ health }: { health: Health | null }) {
  if (health && health.status === "ok") {
    return (
      <div className="mt-6 flex flex-wrap items-center gap-2 rounded-xl border border-[#1d4436] bg-[#0f1f19] px-4 py-2.5 text-xs text-[#9ff0cd]">
        <span aria-hidden className="size-1.5 rounded-full bg-[#10b981]" />
        <span className="font-medium">Pipeline ready</span>
        <span className="text-[#6ee7b7]/70">
          ASR <strong className="font-semibold">{health.providers.asr ?? "—"}</strong> · diarization{" "}
          <strong className="font-semibold">{health.providers.diarization ?? "—"}</strong> · queue{" "}
          <strong className="font-semibold">{health.worker.mode}</strong>
          {health.ffmpeg ? " · ffmpeg ok" : ""}
        </span>
        {health.providers.asr === "mock" && (
          <span className="ml-auto rounded-md border border-[#463a1e] bg-[#211d14] px-2 py-0.5 text-[#e8d9a8]">
            mock provider — deterministic demo transcripts, set ECHONEURA_ASR_PROVIDER for real ASR
          </span>
        )}
      </div>
    );
  }

  if (health) {
    return (
      <div className="mt-6 rounded-xl border border-[#463a1e] bg-[#1a1710] px-4 py-3 text-xs text-[#e8d9a8]">
        <p className="font-medium">Pipeline degraded</p>
        <ul className="mt-1.5 space-y-0.5 text-[#d6c08a]">
          {health.missing_config.map((item) => (
            <li key={item}>
              <span className="font-mono">{item}</span> is not set
            </li>
          ))}
          {!health.ffmpeg && <li>ffmpeg could not be found — install imageio-ffmpeg</li>}
        </ul>
      </div>
    );
  }

  return (
    <div className="mt-6 rounded-xl border border-[#5a2226] bg-[#1a0f11] px-4 py-3 text-xs text-[#fca5a5]">
      <p className="font-medium">Cannot reach the API backend.</p>
      <p className="mt-1 text-[#f1a3a3]/80">
        Start it with <code className="font-mono">make dev-backend</code> (or{" "}
        <code className="font-mono">cd backend &amp;&amp; uvicorn app.main:app --host 0.0.0.0 --port 8000</code>
        ), then reload.
      </p>
    </div>
  );
}
