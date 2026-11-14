import { GoalsList } from "@/components/GoalsList";

export default function GoalsPage() {
  return (
    <main className="page">
      <h1>My goals</h1>
      <p className="lead">
        What GoalPulse is watching for you. A verified offer means it met every condition when checked — nothing is
        bought until you do it yourself.
      </p>
      <GoalsList />
    </main>
  );
}
