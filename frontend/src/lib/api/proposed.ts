import type { ProposedApi } from "../proposed.ts";
import { API_MODE, getApi } from "./index.ts";

/**
 * ICP-02 screens (notifications, prepared purchases) work in mock mode only.
 * In gateway mode this returns null and the UI explains the endpoints are pending:
 * we never call routes that aren't in the approved gateway contract.
 */
export function getProposedApi(): ProposedApi | null {
  if (API_MODE !== "mock") return null;
  return getApi() as unknown as ProposedApi;
}
