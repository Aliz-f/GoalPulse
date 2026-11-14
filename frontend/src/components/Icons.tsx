// All UI icons come from Lucide (ISC licence, inline SVG): they render identically on
// every OS and browser. Decorative icons are aria-hidden; meaning is always in the text.
import {
  Ban,
  BadgeCheck,
  Check,
  CircleExclamationPoint,
  CircleQuestionMark,
  Hourglass,
  Info,
  Pause,
  Radar,
  ScanSearch,
  Search,
  TriangleExclamationPoint,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import type { Lifecycle, OpportunityStatus } from "@/lib/contracts";

export function Icon({ icon: Glyph, size = 16 }: { icon: LucideIcon; size?: number }) {
  return <Glyph size={size} strokeWidth={2} aria-hidden="true" focusable="false" className="icon" />;
}

export const OPPORTUNITY_ICON: Record<OpportunityStatus | "waiting", LucideIcon> = {
  verified: BadgeCheck,
  candidate: ScanSearch,
  unknown: CircleQuestionMark,
  not_found: Search,
  waiting: Hourglass,
};

export const LIFECYCLE_ICON: Record<Lifecycle, LucideIcon> = {
  active: Radar,
  paused: Pause,
  completed: Check,
  cancelled: Ban,
};

type Tone = "error" | "warning" | "info";
const TONE_ICON: Record<Tone, LucideIcon> = {
  error: CircleExclamationPoint,
  warning: TriangleExclamationPoint,
  info: Info,
};

/** Inline message box. Errors and warnings are announced to screen readers. */
export function Notice({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <div role={tone === "info" ? undefined : "alert"} className={`notice notice-${tone}`}>
      <Icon icon={TONE_ICON[tone]} size={18} />
      <div className="notice-body">{children}</div>
    </div>
  );
}
