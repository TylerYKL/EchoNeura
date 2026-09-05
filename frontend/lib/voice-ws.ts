/**
 * WebSocket URL resolution for the live-voice endpoint.
 *
 * Candidate order (first that connects wins):
 *   1. NEXT_PUBLIC_VOICE_WS_URL — explicit override (self-hosted / LAN setups)
 *   2. Same-origin /api/voice/stream — proxied by Next rewrites (localhost, and
 *      the sandbox preview if its proxy carries WS upgrades)
 *   3. Sandbox-derived backend host — the preview exposes every listening port
 *      as https://{port}-{id}.e2b.app, so when we're on 3000-{id}.e2b.app we can
 *      reach the API directly on 8000-{id}.e2b.app. Never hardcoded: derived
 *      from window.location at runtime.
 */

export function voiceWsCandidates(sampleRate: number, language: string): string[] {
  const query = `?fmt=pcm_s16le&sample_rate=${sampleRate}&language=${language}&source=browser`;
  const candidates: string[] = [];

  const override = process.env.NEXT_PUBLIC_VOICE_WS_URL;
  if (override) candidates.push(`${override}${query}`);

  if (typeof window !== "undefined") {
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    candidates.push(`${scheme}//${window.location.host}/api/voice/stream${query}`);

    const match = window.location.hostname.match(/^(\d+)-(.+\.e2b\.app)$/);
    if (match) {
      candidates.push(`wss://8000-${match[2]}/api/voice/stream${query}`);
    }
  }

  return candidates;
}
