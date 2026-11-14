import { Suspense } from "react";

import { SessionView } from "@/components/SessionView";

// The session id is only known at request time; with cacheComponents enabled the
// client hook that reads it must sit inside a Suspense boundary.
export default function SessionPage() {
  return (
    <main className="page">
      <Suspense fallback={<p className="hint">Loading your draft…</p>}>
        <SessionView />
      </Suspense>
    </main>
  );
}
