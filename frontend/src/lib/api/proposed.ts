import type { ProposedApi } from "../proposed.ts";
import { API_MODE, getApi } from "./index.ts";

/**
 * Prepared purchases work in mock mode only (see src/lib/proposed.ts). In gateway mode
 * this returns null and nothing links to the package screen.
 */
export function getProposedApi(): ProposedApi | null {
  if (API_MODE !== "mock") return null;
  return getApi() as unknown as ProposedApi;
}
