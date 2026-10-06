import type { NextConfig } from "next";

// The Coinbase wallet connector (RainbowKit) lazily imports x402 payment packages that are not installed and not used here
const UNUSED_X402 = ['@x402/core/client', '@x402/evm', '@x402/evm/exact/client', '@x402/evm/upto/client', '@x402/svm/exact/client'];

const nextConfig: NextConfig = {
  turbopack: {
    resolveAlias: Object.fromEntries(UNUSED_X402.map((name) => [name, './src/lib/emptyModule.ts'])),
  },
  async rewrites() {
    // A trailing slash in the env var would make the proxied path "//api/...", which the backend answers with 404
    const backendUrl = (process.env.NEXT_PUBLIC_API_BASE_URL || process.env.BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
    return [
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
  async redirects() {
    return [
      {
        source: '/x',
        destination: 'https://x.com/EpochLabsHQ',
        permanent: false,
      },
      {
        source: '/twitter',
        destination: 'https://x.com/EpochLabsHQ',
        permanent: false,
      },
    ];
  },
};

export default nextConfig;

