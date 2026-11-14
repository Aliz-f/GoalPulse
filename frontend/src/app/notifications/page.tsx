import { NotificationsFeed } from "@/components/NotificationsFeed";

export default function NotificationsPage() {
  return (
    <main className="page page-narrow">
      <h1>Notifications</h1>
      <p className="lead">Verified offers for your goals. GoalPulse never orders anything for you.</p>
      <NotificationsFeed />
    </main>
  );
}
