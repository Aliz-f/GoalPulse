"use client";

import Link from "next/link";
import { Bell, LogOut, Plus, Radar, Target, UserRound } from "lucide-react";
import { useState } from "react";

import { useSession } from "@/components/AuthGate";
import { Icon } from "@/components/Icons";
import { API_MODE, GATEWAY_URL, sessionStore } from "@/lib/api";

export function AppHeader() {
  return (
    <header className="app-header">
      <Link href="/" className="brand with-icon">
        <Icon icon={Radar} size={20} />
        GoalPulse
      </Link>
      <nav className="nav" aria-label="Main">
        <Link href="/" className="with-icon">
          <Icon icon={Plus} />
          New goal
        </Link>
        <Link href="/goals" className="with-icon">
          <Icon icon={Target} />
          My goals
        </Link>
        {/* Notifications use the Phase 2 extension API; wired in gateway mode by R2-D2-04. */}
        {API_MODE === "mock" && (
          <Link href="/notifications" className="with-icon">
            <Icon icon={Bell} />
            Notifications
          </Link>
        )}
      </nav>
      {API_MODE === "mock" ? (
        <span className="badge badge-mock" title="Responses are recorded from the Goal Intelligence service; your text is not interpreted.">
          Mock mode · scripted replies
        </span>
      ) : (
        <Account />
      )}
    </header>
  );
}

function Account() {
  const state = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  if (state.status !== "signed_in" || !sessionStore) return null;
  const store = sessionStore;

  async function signOut() {
    setBusy(true);
    setError(false);
    try {
      await store.signOut();
    } catch {
      // The cookie may still be valid, so don't pretend the user is signed out.
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <span className="badge with-icon" title={`Signed in through ${GATEWAY_URL}`}>
        <Icon icon={UserRound} size={14} />
        {state.user.display_name}
      </span>
      <button type="button" className="link-button with-icon" disabled={busy} onClick={() => void signOut()}>
        <Icon icon={LogOut} size={14} />
        {busy ? "Signing out…" : "Sign out"}
      </button>
      {error && (
        <span role="alert" className="hint">
          Sign-out failed; try again.
        </span>
      )}
    </div>
  );
}
