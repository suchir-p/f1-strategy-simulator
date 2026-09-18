import type { NextConfig } from "next";

// The browser talks to /api/*, which Next.js forwards to the FastAPI backend.
// Same origin for the browser, so the backend needs no CORS configuration.
const API_URL = process.env.F1SIM_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  // Docker builds a self-contained server (frontend/Dockerfile sets NEXT_OUTPUT).
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default config;
