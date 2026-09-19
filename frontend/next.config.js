/** @type {import('next').NextConfig} */
const apiProxyTarget = process.env.API_PROXY_TARGET || "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  // Standalone output keeps the prod container image minimal (no node_modules).
  output: "standalone",

  // Containers sit behind TLS-terminating proxies; trust their forwarded proto
  // so redirects and asset URLs stay https in production.
  poweredByHeader: false,

  async rewrites() {
    // Proxy the versioned API to the backend. Same-origin from the browser's
    // point of view: no CORS, portable across preview/live hosts.
    return [
      {
        source: "/api/v1/tiles/:path*",
        destination: `${apiProxyTarget}/api/v1/tiles/:path*`,
      },
      {
        source: "/api/v1/:path*",
        destination: `${apiProxyTarget}/api/v1/:path*`,
      },
    ];
  },

  // MapLibre GL v4 ships plain JS — ensure it isn't pre-bundled twice.
  transpilePackages: [],
};

module.exports = nextConfig;
