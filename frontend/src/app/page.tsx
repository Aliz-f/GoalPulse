import { NewGoalForm } from "@/components/NewGoalForm";

export default function Home() {
  return (
    <main className="page page-narrow">
      <h1>Tell GoalPulse what you&apos;re after</h1>
      <p className="lead">
        Describe it in your own words. GoalPulse turns it into a precise goal, watches the web for offers that really
        meet it, and tells you when it finds one.
      </p>
      <NewGoalForm />
    </main>
  );
}
