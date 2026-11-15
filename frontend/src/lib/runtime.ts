// Runtime selection for the browser app (R2-D2-02, ADR-0003 MVP-01/MVP-06).
//
// Fail closed: there is no default mode or profile. A missing or unknown value throws,
// so a build can never quietly fall back to the in-memory mock. The demo profile only
// runs against the real gateway. next.config.ts applies the same rules before
// `next dev` / `next build` start, so a bad configuration stops there with this message.
//
// Only NEXT_PUBLIC_* values are read. They are inlined into the browser bundle and hold
// no secrets: runtime selectors, the public gateway origin, and credentials for the
// intentionally public, isolated demo accounts.

export const PROFILES = ["test", "dev", "demo"] as const;
export const MODES = ["mock", "gateway"] as const;

export type Profile = (typeof PROFILES)[number];
export type ApiMode = (typeof MODES)[number];

export interface DemoCredential {
  label: string;
  username: string;
  password: string;
}

export interface Runtime {
  profile: Profile;
  mode: ApiMode;
  /** Normalized public gateway origin; null only in mock mode. */
  gatewayUrl: string | null;
  /** Public demo-only credentials offered on the sign-in screen. */
  demoCredentials: DemoCredential[];
}

export interface RuntimeEnv {
  NEXT_PUBLIC_GOALPULSE_PROFILE?: string;
  NEXT_PUBLIC_API_MODE?: string;
  NEXT_PUBLIC_GATEWAY_URL?: string;
  NEXT_PUBLIC_DEMO_USER_A_USERNAME?: string;
  NEXT_PUBLIC_DEMO_USER_A_PASSWORD?: string;
  NEXT_PUBLIC_DEMO_USER_B_USERNAME?: string;
  NEXT_PUBLIC_DEMO_USER_B_PASSWORD?: string;
}

export class RuntimeConfigError extends Error {
  constructor(message: string) {
    super(`GoalPulse frontend configuration: ${message}`);
    this.name = "RuntimeConfigError";
  }
}

export function normalizeBaseUrl(raw: string | undefined): string {
  const value = (raw ?? "").trim();
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new RuntimeConfigError(
      "NEXT_PUBLIC_GATEWAY_URL must be the http(s) origin of the public gateway, e.g. http://localhost:8000",
    );
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username !== "" ||
    url.password !== "" ||
    url.pathname !== "/" ||
    url.search !== "" ||
    url.hash !== ""
  ) {
    throw new RuntimeConfigError(
      "NEXT_PUBLIC_GATEWAY_URL must be the http(s) origin of the public gateway, without credentials, path, query or fragment",
    );
  }
  return url.origin;
}

function pick<T extends string>(name: string, raw: string | undefined, allowed: readonly T[]): T {
  const value = (raw ?? "").trim();
  if (!value) throw new RuntimeConfigError(`${name} is not set; use one of ${allowed.join(", ")}`);
  if (!(allowed as readonly string[]).includes(value)) {
    throw new RuntimeConfigError(`${name}=${JSON.stringify(value)} is not one of ${allowed.join(", ")}`);
  }
  return value as T;
}

export function resolveRuntime(env: RuntimeEnv): Runtime {
  const profile = pick("NEXT_PUBLIC_GOALPULSE_PROFILE", env.NEXT_PUBLIC_GOALPULSE_PROFILE, PROFILES);
  const mode = pick("NEXT_PUBLIC_API_MODE", env.NEXT_PUBLIC_API_MODE, MODES);
  if (profile === "demo" && mode !== "gateway") {
    throw new RuntimeConfigError("profile 'demo' requires NEXT_PUBLIC_API_MODE=gateway; the mock is refused");
  }
  const configuredCredentials = [
    { label: "Demo account A", username: env.NEXT_PUBLIC_DEMO_USER_A_USERNAME?.trim() ?? "", password: env.NEXT_PUBLIC_DEMO_USER_A_PASSWORD ?? "" },
    { label: "Demo account B", username: env.NEXT_PUBLIC_DEMO_USER_B_USERNAME?.trim() ?? "", password: env.NEXT_PUBLIC_DEMO_USER_B_PASSWORD ?? "" },
  ];
  if (profile === "demo" && configuredCredentials.some((credential) => !credential.username || !credential.password)) {
    throw new RuntimeConfigError("demo requires both public demo usernames and passwords");
  }
  const demoCredentials = mode === "gateway"
    ? configuredCredentials.filter((credential) => credential.username && credential.password)
    : [];
  return {
    profile,
    mode,
    gatewayUrl: mode === "gateway" ? normalizeBaseUrl(env.NEXT_PUBLIC_GATEWAY_URL) : null,
    demoCredentials,
  };
}
