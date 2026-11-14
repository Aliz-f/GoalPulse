// Client for the demo sign-in endpoints in the accepted additive contract
// contracts/openapi/gateway.phase2.extension.v1.yaml (ADR-0003 MVP-06):
//   POST /v2/demo/sessions         DemoLogin -> DemoUser, sets the HttpOnly cookie
//   GET  /v2/demo/session          current DemoUser, 401 when signed out
//   POST /v2/demo/sessions/logout  204, clears the cookie
// The session secret lives only in that cookie. Nothing here can read it, and the
// password is sent once, in the POST body, and never kept.

import { ApiError } from "./errors.ts";
import { createHttp, readJson, type HttpOptions } from "./http.ts";

/** components.schemas.DemoLogin */
export interface DemoLogin {
  username: string;
  password: string;
}

/** components.schemas.DemoUser: non-secret identity only. */
export interface DemoUser {
  user_id: string;
  display_name: string;
  expires_at: string;
}

export interface DemoAuthApi {
  /** The signed-in user, or null when there is no valid session. */
  current(): Promise<DemoUser | null>;
  signIn(username: string, password: string): Promise<DemoUser>;
  signOut(): Promise<void>;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Validates the body and keeps only the three contract fields, whatever else arrives. */
export function parseDemoUser(body: unknown): DemoUser {
  const b = (body ?? {}) as Record<string, unknown>;
  const ok =
    typeof b.user_id === "string" &&
    UUID.test(b.user_id) &&
    typeof b.display_name === "string" &&
    b.display_name.trim().length > 0 &&
    b.display_name.length <= 200 &&
    typeof b.expires_at === "string" &&
    !Number.isNaN(Date.parse(b.expires_at));
  if (!ok) throw new ApiError(200, "server", "the gateway sent an unexpected sign-in response");
  return { user_id: b.user_id as string, display_name: b.display_name as string, expires_at: b.expires_at as string };
}

export function createDemoAuthApi(opts: Omit<HttpOptions, "onUnauthorized">): DemoAuthApi {
  // No onUnauthorized here: on these routes 401 means "wrong password" or "no session".
  const http = createHttp(opts);

  return {
    async current() {
      try {
        return parseDemoUser(await readJson(await http.send("GET", "/v2/demo/session")));
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    async signIn(username, password) {
      const body: DemoLogin = { username, password };
      return parseDemoUser(await readJson(await http.send("POST", "/v2/demo/sessions", body)));
    },
    async signOut() {
      try {
        await http.send("POST", "/v2/demo/sessions/logout");
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return; // already signed out
        throw err;
      }
    },
  };
}

/** What the sign-in form says for each failure. Never echoes the password. */
export function signInMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return "Sign-in failed. Please try again.";
  switch (err.status) {
    case 401:
      return "That username and password don't match a demo account.";
    case 403:
      return "The gateway refused this sign-in because it came from a page it doesn't trust. Open GoalPulse from its usual address and try again.";
    case 429:
      return "Too many sign-in attempts. Wait a minute, then try again.";
    case 503:
      return "Demo sign-in is turned off on this gateway.";
    case 0:
      return "Couldn't reach the GoalPulse gateway. Check that it's running and try again.";
    default:
      return "Sign-in failed because of a gateway problem. Please try again.";
  }
}
