"use client";

import { LogIn } from "lucide-react";
import { Fragment, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";

import { Icon, Notice } from "@/components/Icons";
import { sessionStore } from "@/lib/api";
import { signInMessage } from "@/lib/api/demo-auth";
import { CHECKING, type SessionState, type SessionStore } from "@/lib/session";

const NO_STORE = () => () => {};

/** The current sign-in state; "checking" during server rendering and in mock mode. */
export function useSession(): SessionState {
  return useSyncExternalStore(
    sessionStore?.subscribe ?? NO_STORE,
    () => sessionStore?.get() ?? CHECKING,
    () => CHECKING,
  );
}

/**
 * Gateway mode: pages render only for a signed-in user, and are remounted when the
 * account changes so nothing loaded for one user is shown to the next. Mock mode has
 * no accounts and passes straight through.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  return sessionStore ? <GatewayGate store={sessionStore}>{children}</GatewayGate> : <>{children}</>;
}

function GatewayGate({ store, children }: { store: SessionStore; children: ReactNode }) {
  const state = useSession();

  useEffect(() => {
    if (store.get().status === "checking") void store.refresh();
  }, [store]);

  if (state.status === "signed_in") return <Fragment key={state.user.user_id}>{children}</Fragment>;

  return (
    <main className="page page-narrow">
      {state.status === "checking" && <p className="hint">Checking your sign-in…</p>}
      {state.status === "unavailable" && (
        <Notice tone="error">
          {state.message}{" "}
          <button type="button" className="link-button" onClick={() => void store.refresh()}>
            Try again
          </button>
        </Notice>
      )}
      {state.status === "signed_out" && <SignInForm store={store} expired={state.expired} />}
    </main>
  );
}

function SignInForm({ store, expired }: { store: SessionStore; expired: boolean }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (busy || !username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      await store.signIn(username.trim(), password);
    } catch (err) {
      setError(signInMessage(err));
      setBusy(false);
    } finally {
      // The password is sent once and never kept, not even after a failed attempt.
      setPassword("");
    }
  }

  return (
    <>
      <h1>Sign in to GoalPulse</h1>
      <p className="lead">
        This is a local demo with two prepared accounts. Each account sees only its own goals.
      </p>
      {expired && <Notice tone="warning">Your session ended. Sign in again to continue.</Notice>}
      <form
        className="card sign-in"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label htmlFor="demo-username" className="field-label">
          Username
        </label>
        <input
          id="demo-username"
          name="username"
          autoComplete="username"
          maxLength={128}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <label htmlFor="demo-password" className="field-label">
          Password
        </label>
        <input
          id="demo-password"
          name="password"
          type="password"
          autoComplete="current-password"
          maxLength={1024}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <Notice tone="error">{error}</Notice>}
        <div className="actions">
          <button type="submit" className="primary with-icon" disabled={busy || !username.trim() || !password}>
            <Icon icon={LogIn} />
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>
    </>
  );
}
