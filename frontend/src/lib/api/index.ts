import { getToken, onTokenChange } from "../auth.ts";
import type { GoalDraft } from "../contracts.ts";
import { createGatewayApi } from "./gateway.ts";
import { createMockApi } from "./mock.ts";
import type { GoalPulseApi } from "./types.ts";

// NEXT_PUBLIC_* values are inlined at build time. Only the public gateway URL lives
// here: no private service URL, service token or provider key ever reaches the browser.
export const API_MODE: "mock" | "gateway" = process.env.NEXT_PUBLIC_API_MODE === "gateway" ? "gateway" : "mock";
export const GATEWAY_URL = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "";

let api: GoalPulseApi | null = null;

export function getApi(): GoalPulseApi {
  if (!api) {
    api = API_MODE === "gateway" ? createGatewayApi({ baseUrl: GATEWAY_URL, getToken }) : createMockApi();
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

if (typeof window !== "undefined") onTokenChange(() => cache.clear());
