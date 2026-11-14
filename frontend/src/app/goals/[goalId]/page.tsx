import { Suspense } from "react";

import { GoalDetail } from "@/components/GoalDetail";

// goalId is only known at request time; with cacheComponents the client hook
// reading it must render inside a Suspense boundary.
export default function GoalPage() {
  return (
    <main className="page">
      <Suspense fallback={<p className="hint">Loading goal…</p>}>
        <GoalDetail />
      </Suspense>
    </main>
  );
}
