import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: [
    ".repl.co",
    "*.repl.co",
    ".replit.dev",
    "*.replit.dev",
  ],
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: "http://localhost:8000/api/v1/:path*",
      },
    ];
  },
};

export default nextConfig;
