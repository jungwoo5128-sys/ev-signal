import type { NextConfig } from "next";

// 브라우저는 /api/* 만 호출하고, Next 서버가 FastAPI로 전달한다 (CORS 불필요).
const API_URL = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
