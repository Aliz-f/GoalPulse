import { Suspense } from "react";

import { PackageView } from "@/components/PackageView";

export default function PackagePage() {
  return (
    <main className="page page-narrow">
      <Suspense fallback={<p className="hint">Loading prepared purchase…</p>}>
        <PackageView />
      </Suspense>
    </main>
  );
}
