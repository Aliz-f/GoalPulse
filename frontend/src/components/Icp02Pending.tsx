import { Notice } from "@/components/Icons";

/** Shown in gateway mode for screens whose API isn't wired yet (prepared purchases, P1). */
export function Icp02Pending({ what }: { what: string }) {
  return (
    <div className="card">
      <Notice tone="info">
        {what} aren&apos;t available yet. GoalPulse only shows them once their gateway endpoint is connected, rather than
        showing a sample. Your notifications and goal pages show what was verified.
      </Notice>
    </div>
  );
}
