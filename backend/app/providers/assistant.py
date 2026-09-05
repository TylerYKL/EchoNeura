"""Assistant bridge — the "next actions" hook for live voice (ADR 0004).

Flow:   mic audio -> ASR provider -> transcript -> AssistantProvider.act()
        -> AssistantResponse {reply, action, data}

The product decision (which LLM answers, what actions exist) is deliberately
deferred, so today only a deterministic mock is implemented. The seam is what
matters: wiring OpenAI/Anthropic/a local model later means adding one adapter
and flipping ECHONEURA_ASSISTANT_PROVIDER — nothing else moves.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class AssistantResponse:
    """What the assistant wants to happen next.

    reply   text to speak back (TTS) or show in the UI
    action  machine-readable verb for clients: "none" | "speak" | "navigate" |
            "run_hook" — clients decide what to do with it
    data    structured payload for the action (e.g. {"url": ...} for navigate)
    """

    reply: str
    action: str = "none"
    data: dict[str, Any] = field(default_factory=dict)
    provider: str = "mock"


class AssistantProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def act(
        self,
        transcript: str,
        *,
        language: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> AssistantResponse: ...

    def check_available(self) -> None:
        return None


_PUNCT = re.compile(r"[^\w\s]+")


class MockAssistantProvider(AssistantProvider):
    """Deterministic rule-based assistant for demos, CI and protocol testing.

    Understands a handful of voice commands so the end-to-end loop
    (speak -> transcribe -> act -> reply) is demonstrable without an LLM key:

      "what time is it"        -> speaks the current UTC time
      "what's the date"        -> speaks today's date
      "help" / "what can you"  -> capability list
      "repeat after me: X"     -> echoes X back
      "open X"                 -> action="navigate" with a search URL
      anything else            -> acknowledges with the transcript verbatim
    """

    name = "mock"

    def act(
        self,
        transcript: str,
        *,
        language: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> AssistantResponse:
        text = (transcript or "").strip()
        normalized = _PUNCT.sub("", text).lower()

        if not normalized:
            return AssistantResponse(
                reply="I didn't catch that — could you say it again?",
                action="speak",
                provider=self.name,
            )

        if re.search(r"\b(what\s+time|current\s+time|time\s+is\s+it)\b", normalized):
            now = datetime.now(UTC).strftime("%H:%M UTC")
            return AssistantResponse(
                reply=f"It is {now}.", action="speak", data={"utc": now}, provider=self.name
            )

        if re.search(r"\b(what.?s\s+the\s+date|today.?s\s+date|what\s+day)\b", normalized):
            today = datetime.now(UTC).strftime("%A, %d %B %Y")
            return AssistantResponse(
                reply=f"Today is {today}.",
                action="speak",
                data={"date": today},
                provider=self.name,
            )

        if re.search(r"\b(help|what\s+can\s+you\s+do)\b", normalized):
            return AssistantResponse(
                reply=(
                    "I am the mock assistant. Try: what time is it, what's the date, "
                    "repeat after me: hello, or open EchoNeura. Wire a real LLM by "
                    "setting ECHONEURA_ASSISTANT_PROVIDER."
                ),
                action="speak",
                provider=self.name,
            )

        match = re.search(r"\brepeat\s+after\s+me[:\s]+(?P<phrase>.+)$", normalized)
        if match:
            phrase = match.group("phrase").strip()
            return AssistantResponse(
                reply=phrase.capitalize(), action="speak", data={"echo": phrase}, provider=self.name
            )

        match = re.search(r"\bopen\s+(?P<target>.+)$", normalized)
        if match:
            target = match.group("target").strip()
            return AssistantResponse(
                reply=f"Opening {target}.",
                action="navigate",
                data={
                    "query": target,
                    "url": f"https://duckduckgo.com/?q={target.replace(' ', '+')}",
                },
                provider=self.name,
            )

        return AssistantResponse(
            reply=(
                f'I heard: "{text}". This is the mock assistant — set '
                "ECHONEURA_ASSISTANT_PROVIDER (see ADR 0004) to route transcripts "
                "to a real LLM for next actions."
            ),
            action="none",
            data={"transcript": text, "language": language},
            provider=self.name,
        )
