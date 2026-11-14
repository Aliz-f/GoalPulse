export type ApiErrorKind =
  | "not_signed_in"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "invalid"
  | "unavailable"
  | "network"
  | "server";

export class ApiError extends Error {
  readonly status: number;
  readonly kind: ApiErrorKind;
  /** Present on 409 responses from Goal Intelligence (stale revision). */
  readonly currentRevision: number | null;

  constructor(status: number, kind: ApiErrorKind, message: string, currentRevision: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
    this.currentRevision = currentRevision;
  }
}

export function kindForStatus(status: number): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 422) return "invalid";
  if (status === 429 || status === 503) return "unavailable";
  return "server";
}

export interface ErrorAdvice {
  message: string;
  /** Keep what the user typed so they can resend it. */
  keepInput: boolean;
  /** Reload the current draft from the server (stale view). */
  reload: boolean;
}

/** One place that decides what the UI says and does for each failure. */
export function adviceFor(err: unknown, action: "send" | "confirm" | "load" | "create" | "lifecycle"): ErrorAdvice {
  if (!(err instanceof ApiError)) {
    return { message: "Something went wrong. Nothing was changed; please try again.", keepInput: true, reload: false };
  }
  switch (err.kind) {
    case "conflict":
      if (action === "lifecycle") {
        return { message: "That change isn't possible from the goal's current state. We reloaded it.", keepInput: false, reload: true };
      }
      if (action === "confirm") {
        return {
          message:
            "This draft changed or can no longer be confirmed. We loaded the latest version — please review it before confirming.",
          keepInput: true,
          reload: true,
        };
      }
      return {
        message: `This conversation moved on${err.currentRevision ? ` (now revision ${err.currentRevision})` : ""}, maybe in another tab. We loaded the latest version; your message is still in the box if you want to send it.`,
        keepInput: true,
        reload: true,
      };
    case "not_signed_in":
    case "unauthorized":
      return { message: "You're not signed in, or your session expired. Sign in and try again.", keepInput: true, reload: false };
    case "forbidden":
    case "not_found":
      return {
        message: action === "lifecycle" || action === "load" ? "This doesn't exist or isn't yours." : "This goal draft doesn't exist or isn't yours.",
        keepInput: false,
        reload: false,
      };
    case "invalid":
      return { message: err.message || "That message couldn't be accepted. Please shorten or rephrase it.", keepInput: true, reload: false };
    case "unavailable":
      return { message: "The assistant is busy right now. Nothing was changed; please send it again in a moment.", keepInput: true, reload: false };
    case "network":
      return { message: "Couldn't reach GoalPulse. Check your connection and try again.", keepInput: true, reload: false };
    default:
      return { message: "GoalPulse had a problem. Nothing was changed; please try again.", keepInput: true, reload: false };
  }
}
