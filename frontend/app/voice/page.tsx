import type { Metadata } from "next";

import { VoiceClient } from "@/components/VoiceClient";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Live voice",
  description:
    "Hold-to-talk streaming transcription: mic audio streams over WebSocket to EchoNeura's ASR and assistant bridge.",
};

export default function VoicePage() {
  return (
    <>
      <section className="mt-10 max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          Talk to <span className="text-[var(--color-accent-soft)]">EchoNeura</span>.
        </h1>
        <p className="mt-3 text-[15px] leading-relaxed text-[var(--color-mute)]">
          Live-voice ingest (M1.5): your microphone streams raw PCM over a WebSocket; the backend
          transcribes it with the configured ASR provider and passes the text to the assistant
          bridge for next actions. The assistant is currently the deterministic mock — wire a real
          LLM via <code className="font-mono text-xs">ECHONEURA_ASSISTANT_PROVIDER</code>.
        </p>
      </section>
      <VoiceClient />
    </>
  );
}
