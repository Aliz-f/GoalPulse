import { Notice } from "@/components/Icons";

export function Icp02Pending({ what }: { what: string }) {
  return (
    <div className="card">
      <Notice tone="info">
        {what} aren&apos;t available yet. They need public gateway endpoints that are still a proposal (ICP-02), and
        GoalPulse only calls approved endpoints. Switch to mock mode to preview this screen.
      </Notice>
    </div>
  );
}
