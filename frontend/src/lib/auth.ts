// INTERIM sign-in until ICP-08 picks the identity provider (Developer 1).
// The browser only ever holds the end user's own access token and sends it to the
// public gateway as a Bearer token. It is kept in memory and in sessionStorage
// (cleared when the tab closes), never in localStorage or a readable cookie, and
// it is never logged. Changing or clearing it clears all cached drafts.

const STORAGE_KEY = "goalpulse.accessToken";

let token: string | null = null;
let loaded = false;
const listeners = new Set<() => void>();

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function getToken(): string | null {
  if (!loaded) {
    loaded = true;
    token = storage()?.getItem(STORAGE_KEY) ?? null;
  }
  return token;
}

export function setToken(next: string | null): void {
  const clean = next?.trim() || null;
  if (clean === getToken()) return;
  token = clean;
  try {
    if (clean) storage()?.setItem(STORAGE_KEY, clean);
    else storage()?.removeItem(STORAGE_KEY);
  } catch {
    // storage may be unavailable (private mode); memory still works
  }
  listeners.forEach((fn) => fn());
}

export function onTokenChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
