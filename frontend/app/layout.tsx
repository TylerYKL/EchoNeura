import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "EchoNeura — AI Audio Content Studio",
    template: "%s · EchoNeura",
  },
  description:
    "Upload a recording, get a speaker-separated transcript you can correct, then export SRT, WebVTT, TXT, Markdown or JSON.",
};

export const viewport: Viewport = {
  themeColor: "#07070a",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <div className="mx-auto flex min-h-screen w-full max-w-[1400px] flex-col px-4 pb-16 sm:px-6">
          <SiteHeader />
          <main className="flex-1">{children}</main>
          <SiteFooter />
        </div>
      </body>
    </html>
  );
}

function SiteHeader() {
  return (
    <header className="flex items-center justify-between border-b border-[var(--color-line-soft)] py-5">
      <a href="/" className="group flex items-center gap-3">
        <span
          aria-hidden
          className="grid size-9 place-items-center rounded-xl bg-[var(--color-accent)] text-sm font-bold text-white shadow-[0_0_24px_-6px_var(--color-accent)]"
        >
          EN
        </span>
        <span className="leading-tight">
          <span className="block text-[15px] font-semibold tracking-tight text-white">
            EchoNeura
          </span>
          <span className="block text-[11px] uppercase tracking-[0.16em] text-[var(--color-mute)]">
            Audio Content Studio
          </span>
        </span>
      </a>
      <nav className="flex items-center gap-1 text-sm">
        <a href="/" className="btn btn-ghost btn-sm">
          Studio
        </a>
        <a href="/api/docs" className="btn btn-ghost btn-sm" target="_blank" rel="noreferrer">
          API docs
        </a>
      </nav>
    </header>
  );
}

function SiteFooter() {
  return (
    <footer className="mt-14 border-t border-[var(--color-line-soft)] pt-5 text-xs text-[var(--color-mute)]">
      <p>
        M1 — upload · transcribe · diarize · correct · export. Audio never leaves this stack unless
        you configure a cloud provider.
      </p>
    </footer>
  );
}
