import type { GoalDraft, GoalSpec, GoalSummary, Lifecycle, LifecycleAction } from "../contracts.ts";
import { lifecycleAfter } from "../goals.ts";
import { ApiError } from "./errors.ts";
import { MOCK_SCRIPTS, type MockScript } from "./mock-scripts.ts";
import type { GoalPulseApi, MockControls } from "./types.ts";

// Mock mode replays conversations recorded from the real Goal Intelligence
// service. It does NOT interpret what you type: every reply advances the script.
// The UI labels this clearly so nobody mistakes it for live behaviour.

const MOCK_USER = "00000000-0000-4000-8000-00000000000d";

interface MockSession {
  id: string;
  script: MockScript;
  step: number;
  revision: number;
  state: GoalDraft["state"];
}

export interface MockOptions {
  delayMs?: number;
  newId?: () => string;
  now?: () => Date;
  /** Start with two sample goals (one verified, one lacking evidence). Default true. */
  seedGoals?: boolean;
}

interface MockGoal {
  spec: GoalSpec;
  summary: GoalSummary;
  createdAt: number;
}

type CandidateParts = Omit<GoalSpec, "goal_id" | "owner_user_id" | "version" | "approved_at">;

function specFrom(candidate: CandidateParts, goalId: string, approvedAt: Date): GoalSpec {
  return {
    goal_id: goalId,
    owner_user_id: MOCK_USER,
    version: 1,
    title: candidate.title,
    goal_type: candidate.goal_type,
    criteria: candidate.criteria,
    monitoring: candidate.monitoring,
    actions: { notify_user: candidate.actions.notify_user, agent_handoff: "prepare_only", auto_purchase: false },
    approved_at: approvedAt.toISOString().replace(/\.\d{3}Z$/, "Z"),
  };
}

function readyCandidate(name: MockScript["name"]): CandidateParts {
  const script = MOCK_SCRIPTS.find((s) => s.name === name)!;
  return structuredClone(script.steps[script.steps.length - 1].draft.candidate_goal) as unknown as CandidateParts;
}

export function pickScript(initialMessage: string): MockScript {
  const t = initialMessage.toLowerCase();
  const name: MockScript["name"] = /\b(flights?|hotels?|travel|trip|holiday|vacation|jobs?)\b/.test(t)
    ? "unsupported"
    : /caf[eé]|monthly|every month|supplies|restaurant/.test(t)
      ? "procurement"
      : "product";
  return MOCK_SCRIPTS.find((s) => s.name === name) ?? MOCK_SCRIPTS[0];
}

export function createMockApi(opts: MockOptions = {}): GoalPulseApi & MockControls {
  const sessions = new Map<string, MockSession>();
  const goals = new Map<string, MockGoal>();
  const delay = opts.delayMs ?? 350;
  const newId = opts.newId ?? (() => crypto.randomUUID());
  const now = opts.now ?? (() => new Date());

  function addGoal(spec: GoalSpec, summary: Partial<GoalSummary>, createdAt: number) {
    goals.set(spec.goal_id, {
      spec,
      createdAt,
      summary: {
        goal_id: spec.goal_id,
        title: spec.title,
        version: spec.version,
        lifecycle: "active",
        opportunity_status: "unknown",
        last_evaluated_at: null,
        ...summary,
      },
    });
  }

  if (opts.seedGoals ?? true) {
    const t = now().getTime();
    const iso = (msAgo: number) => new Date(t - msAgo).toISOString().replace(/\.\d{3}Z$/, "Z");
    addGoal(specFrom(readyCandidate("product"), newId(), new Date(t - 86_400_000)), {
      opportunity_status: "verified",
      last_evaluated_at: iso(25 * 60_000),
    }, t - 86_400_000);
    addGoal(specFrom(readyCandidate("procurement"), newId(), new Date(t - 2 * 86_400_000)), {
      opportunity_status: "unknown",
      last_evaluated_at: iso(3 * 3_600_000),
    }, t - 2 * 86_400_000);
  }

  function findGoal(id: string): MockGoal {
    const g = goals.get(id);
    if (!g) throw new ApiError(404, "not_found", "goal not found");
    return g;
  }

  const wait = () => (delay > 0 ? new Promise((r) => setTimeout(r, delay)) : Promise.resolve());

  function view(s: MockSession): GoalDraft {
    const recorded = s.script.steps[s.step].draft;
    const ready = s.state === "ready_for_confirmation";
    return {
      ...structuredClone(recorded),
      session_id: s.id,
      owner_user_id: MOCK_USER,
      revision: s.revision,
      state: s.state,
      ready_for_confirmation: ready,
      missing_fields: ready ? [] : [...recorded.missing_fields],
      next_question: ready ? null : recorded.next_question,
    };
  }

  function get(id: string): MockSession {
    const s = sessions.get(id);
    if (!s) throw new ApiError(404, "not_found", "session not found");
    return s;
  }

  return {
    mode: "mock",

    async createSession(initialMessage) {
      await wait();
      if (initialMessage.trim().length < 3) throw new ApiError(422, "invalid", "message must be at least 3 characters");
      const script = pickScript(initialMessage);
      const s: MockSession = { id: newId(), script, step: 0, revision: 1, state: script.steps[0].draft.state };
      sessions.set(s.id, s);
      return view(s);
    },

    async sendMessage(sessionId, message, expectedRevision) {
      await wait();
      const s = get(sessionId);
      if (!message.trim()) throw new ApiError(422, "invalid", "message must not be empty");
      if (s.state === "confirmed" || s.state === "abandoned") throw new ApiError(409, "conflict", `session is ${s.state}`, s.revision);
      if (expectedRevision !== s.revision) throw new ApiError(409, "conflict", "stale revision", s.revision);
      s.step = Math.min(s.step + 1, s.script.steps.length - 1);
      s.revision += 1;
      s.state = s.script.steps[s.step].draft.state;
      return view(s);
    },

    async getSession(sessionId) {
      await wait();
      return view(get(sessionId));
    },

    async confirm(sessionId, expectedRevision) {
      await wait();
      const s = get(sessionId);
      if (s.revision !== expectedRevision || s.state !== "ready_for_confirmation") {
        throw new ApiError(409, "conflict", "draft incomplete, stale, or already confirmed", s.revision);
      }
      const candidate = structuredClone(s.script.steps[s.step].draft.candidate_goal) as unknown as CandidateParts;
      s.state = "confirmed";
      const created = now();
      const spec = specFrom(candidate, newId(), created);
      addGoal(spec, {}, created.getTime());
      return structuredClone(spec);
    },

    async listGoals(status?: Lifecycle) {
      await wait();
      return [...goals.values()]
        .filter((g) => !status || g.summary.lifecycle === status)
        .sort((a, b) => b.createdAt - a.createdAt)
        .map((g) => ({ ...g.summary }));
    },

    async getGoal(goalId) {
      await wait();
      return structuredClone(findGoal(goalId).spec);
    },

    async updateLifecycle(goalId, action: LifecycleAction) {
      await wait();
      const g = findGoal(goalId);
      const next = lifecycleAfter(g.summary.lifecycle, action);
      if (!next) throw new ApiError(409, "conflict", `can't ${action} a ${g.summary.lifecycle} goal`);
      g.summary.lifecycle = next;
    },

    suggestedReply(sessionId) {
      const s = sessions.get(sessionId);
      if (!s || s.state === "confirmed") return null;
      return s.script.steps[s.step + 1]?.user ?? null;
    },

    simulateConcurrentEdit(sessionId) {
      const s = sessions.get(sessionId);
      if (s && s.state !== "confirmed") s.revision += 1;
    },
  };
}
