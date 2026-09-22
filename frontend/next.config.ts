import type { NextConfig } from "next";

// Single-origin, same trick as recipes/litpanel: the browser only talks to the
// frontend host; Next reverse-proxies /api to the backend Service in-cluster.
// Default points at the local dev backend so `next dev` works unchanged.
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://127.0.0.1:8070";
// 访客（Authorization: Bearer guest）走演示后端：假数据、只读。真实数据在另一个进程和卷里。
const DEMO_BACKEND_ORIGIN = process.env.DEMO_BACKEND_ORIGIN || "http://127.0.0.1:8171";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        has: [{ type: "header", key: "authorization", value: "Bearer guest" }],
        destination: `${DEMO_BACKEND_ORIGIN}/api/:path*`,
      },
      { source: "/api/:path*", destination: `${BACKEND_ORIGIN}/api/:path*` },
    ];
  },
};

export default nextConfig;
