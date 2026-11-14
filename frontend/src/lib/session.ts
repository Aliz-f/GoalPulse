// Who is signed in, for gateway mode (R2-D2-02). Replaces the pasted-token store.
//
// The store only ever holds the non-secret DemoUser the gateway returns. The session
// itself is the gateway's HttpOnly cookie, which this code cannot read. Nothing is
// written to localStorage or sessionStorage: after a refresh or a browser restart the
// store asks the gateway again (GET /v2/demo/session).

import type { DemoAuthApi, DemoUser } from "./api/demo-auth.ts";
import { ApiError } from "./api/errors.ts";

export type SessionState =
  | { status: "checking" }
  | { status: "signed_out"; expired: boolean }
  | { status: "signed_in"; user: DemoUser }
  | { status: "unavailable"; message: string };

export interface SessionStore {
  get(): SessionState;
  subscribe(fn: () => void): () => void;
  /** Ask the gateway who is signed in. Safe to call again after "unavailable". */
  refresh(): Promise<void>;
  /** Throws ApiError on failure so the form can explain it; state stays signed out. */
  signIn(username: string, password: string): Promise<void>;
  /** Throws if the gateway didn't clear the cookie; state then stays signed in. */
  signOut(): Promise<void>;
  /** Any gateway call answered 401: the cookie expired or was revoked. */
  expire(): void;
}

export const CHECKING: SessionState = { status: "checking" };

export function createSessionStore(auth: DemoAuthApi): SessionStore {
  let state: SessionState = CHECKING;
  const listeners = new Set<() => void>();
  let generation = 0;

  function set(next: SessionState) {
    state = next;
    listeners.forEach((fn) => fn());
  }

  return {
    get: () => state,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    async refresh() {
      const mine = ++generation;
      if (state.status !== "checking") set(CHECKING);
      try {
        const user = await auth.current();
        if (mine !== generation) return; // a newer sign-in/out won
        set(user ? { status: "signed_in", user } : { status: "signed_out", expired: false });
      } catch (err) {
        if (mine !== generation) return;
        const message =
          err instanceof ApiError && err.status === 503
            ? "Demo sign-in is turned off on this gateway."
            : err instanceof ApiError && err.kind === "network"
              ? "Couldn't reach the GoalPulse gateway. Check that it's running."
              : "The GoalPulse gateway had a problem checking your sign-in.";
        set({ status: "unavailable", message });
      }
    },
    async signIn(username, password) {
      const mine = ++generation;
      const user = await auth.signIn(username, password);
      if (mine === generation) set({ status: "signed_in", user });
    },
    async signOut() {
      const mine = ++generation;
      await auth.signOut();
      if (mine === generation) set({ status: "signed_out", expired: false });
    },
    expire() {
      if (state.status === "signed_in") {
        generation++;
        set({ status: "signed_out", expired: true });
      }
    },
  };
}

/** The signed-in user's id, or null. Used to drop cached data when the account changes. */
export function userIdOf(state: SessionState): string | null {
  return state.status === "signed_in" ? state.user.user_id : null;
}
