/**
 * Next.js config (Next 16.3.x — patched for CVE-2025-66478).
 *
 * Two things matter for how this app is run:
 *
 * 1. `rewrites` proxies /api/* to the FastAPI backend. The browser only ever
 *    talks to the Next origin, so the app works unchanged behind the sandbox
 *    live-preview host, on localhost, and in production — no CORS, no absolute
 *    URLs, and the backend never needs a public hostname.
 *
 * 2. `allowedDevOrigins` stops Next from rejecting cross-origin dev requests,
 *    which is what would otherwise break the sandbox preview iframe.
 */

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",

  // Allow any *.e2b.app sandbox preview host plus localhost in development.
  allowedDevOrigins: [
    "localhost",
    "127.0.0.1",
    "*.e2b.app",
    ...(process.env.ALLOWED_DEV_ORIGINS ? process.env.ALLOWED_DEV_ORIGINS.split(",") : []),
  ],

  // /api/* -> FastAPI. Next route handlers would take precedence over these,
  // and we deliberately have none: the Python API is the only API.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
      {
        source: "/healthz",
        destination: `${BACKEND_URL}/healthz`,
      },
    ];
  },

  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
