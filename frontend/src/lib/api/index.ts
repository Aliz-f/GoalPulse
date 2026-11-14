import type { GoalDraft } from "../contracts.ts";
import { resolveRuntime, type ApiMode, type Runtime } from "../runtime.ts";
import { createSessionStore, userIdOf, type SessionStore } from "../session.ts";
import { createDemoAuthApi } from "./demo-auth.ts";
import { createGatewayApi } from "./gateway.ts";
import { createMockApi } from "./mock.ts";
import type { GoalPulseApi } from "./types.ts";

// Each NEXT_PUBLIC_* value must be written out literally so Next inlines it at build
// time. resolveRuntime throws on a missing or unknown value, so a misconfigured build
// fails instead of loading the mock (R2-D2-02).
export const RUNTIME: Runtime = resolveRuntime({
  NEXT_PUBLIC_GOALPULSE_PROFILE: process.env.NEXT_PUBLIC_GOALPULSE_PROFILE,
  NEXT_PUBLIC_API_MODE: process.env.NEXT_PUBLIC_API_MODE,
  NEXT_PUBLIC_GATEWAY_URL: process.env.NEXT_PUBLIC_GATEWAY_URL,
});
export const API_MODE: ApiMode = RUNTIME.mode;
export const GATEWAY_URL = RUNTIME.gatewayUrl ?? "";

/** Gateway mode only: who is signed in. Null in mock mode, which has no accounts. */
export const sessionStore: SessionStore | null =
  RUNTIME.gatewayUrl === null ? null : createSessionStore(createDemoAuthApi({ baseUrl: RUNTIME.gatewayUrl }));

let api: GoalPulseApi | null = null;

export function getApi(): GoalPulseApi {
  if (!api) {
    api =
      RUNTIME.gatewayUrl === null
        ? createMockApi()
        : createGatewayApi({
            baseUrl: RUNTIME.gatewayUrl,
            onUnauthorized: () => sessionStore?.expire(),
            canSend: () => sessionStore?.get().status === "signed_in",
          });
  }
  return api;
}

// ---- tiny per-tab cache so navigation does not refetch; cleared on account change

export interface Turn {
  role: "user" | "assistant";
  text: string;
}

export interface CachedSession {
  draft: GoalDraft;
  turns: Turn[];
}

const cache = new Map<string, CachedSession>();

export const sessionCache = {
  get: (id: string) => cache.get(id),
  set: (id: string, value: CachedSession) => void cache.set(id, value),
  clear: () => cache.clear(),
};

if (sessionStore) {
  let lastUser = userIdOf(sessionStore.get());
  sessionStore.subscribe(() => {
    const next = userIdOf(sessionStore.get());
    if (next !== lastUser) cache.clear();
    lastUser = next;
  });
}
