import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Playwright runs its own `next dev` with NEXT_DIST_DIR=.next-e2e. Next 16 holds an
  // exclusive lock inside the dist dir while `next dev` runs, so sharing `.next` with a
  // developer's `npm run dev` made the e2e web server exit 1 (R2-D2-03).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  cacheComponents: true,
  partialPrefetching: true,
};

export default nextConfig;
