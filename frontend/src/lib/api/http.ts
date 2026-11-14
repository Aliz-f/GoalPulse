import { normalizeBaseUrl } from "../runtime.ts";
import { ApiError, kindForStatus } from "./errors.ts";

export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;
export type Method = "GET" | "POST" | "PATCH";

export interface HttpOptions {
  /** Public gateway origin, e.g. http://localhost:8000. Never a private service URL. */
  baseUrl: string;
  fetchImpl?: FetchLike;
  newCorrelationId?: () => string;
  /** Called on any 401, so the app can show sign-in. Not used by the sign-in calls themselves. */
  onUnauthorized?: () => void;
}

export interface Http {
  /** Sends one request and returns the response for a 2xx; throws ApiError otherwise. */
  send(method: Method, path: string, body?: unknown): Promise<Response>;
}

/**
 * One request path for every gateway call (R2-D2-02, ADR-0003 MVP-06).
 *
 * Identity is the gateway's HttpOnly demo session cookie, sent with
 * `credentials: "include"`. The browser never holds, stores or sends a bearer token,
 * and never supplies an owner ID. The browser adds the Origin header to every
 * mutation itself; the gateway checks it against GATEWAY_DEMO_ALLOWED_ORIGIN.
 */
export function createHttp(opts: HttpOptions): Http {
  const base = normalizeBaseUrl(opts.baseUrl);
  const doFetch: FetchLike = opts.fetchImpl ?? ((input, init) => fetch(input, init));
  const correlationId = opts.newCorrelationId ?? (() => crypto.randomUUID());

  return {
    async send(method, path, body) {
      const headers: Record<string, string> = {
        Accept: "application/json",
        "X-Correlation-ID": correlationId(),
      };
      if (body !== undefined) headers["Content-Type"] = "application/json";

      let res: Response;
      try {
        res = await doFetch(`${base}${path}`, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          cache: "no-store",
          credentials: "include",
          redirect: "error",
        });
      } catch {
        throw new ApiError(0, "network", "network error");
      }
      if (res.ok) return res;

      // Error body is the ICP-01 shape {code, message}; the gateway contract doesn't
      // require one, so fall back to the status. A 409 carries no revision: callers
      // re-GET the session to see the latest draft.
      let detail: { code?: unknown; message?: unknown } = {};
      try {
        detail = await res.json();
      } catch {
        // no body
      }
      if (res.status === 401) opts.onUnauthorized?.();
      const message = typeof detail.message === "string" ? detail.message : `HTTP ${res.status}`;
      const code = typeof detail.code === "string" ? detail.code : null;
      throw new ApiError(res.status, kindForStatus(res.status), message, code);
    },
  };
}

/** Reads a JSON body; a 2xx that isn't JSON is a gateway fault, not data. */
export async function readJson<T>(res: Response): Promise<T> {
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError(res.status, "server", "the gateway sent a response that isn't JSON");
  }
}
