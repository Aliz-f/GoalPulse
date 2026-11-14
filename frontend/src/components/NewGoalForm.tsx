"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Notice } from "@/components/Icons";
import { API_MODE, getApi, sessionCache, type Turn } from "@/lib/api";
import { adviceFor } from "@/lib/api/errors";

const MAX_CHARS = 2000;

const EXAMPLES = [
  "I want to buy an RTX 5070 for under €600",
  "Our café needs 20 L lactose-free milk and 10 kg coffee beans every month. We currently spend €250 and want to save 15%",
];

export function NewGoalForm() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = text.trim();
  const valid = trimmed.length >= 3 && trimmed.length <= MAX_CHARS;

  async function submit() {
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const draft = await getApi().createSession(trimmed);
      const turns: Turn[] = [{ role: "user", text: trimmed }];
      if (draft.next_question) turns.push({ role: "assistant", text: draft.next_question });
      sessionCache.set(draft.session_id, { draft, turns });
      router.push(`/sessions/${draft.session_id}`);
    } catch (err) {
      setError(adviceFor(err, "create").message);
      setBusy(false);
    }
  }

  return (
    <form
      className="card"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label htmlFor="goal" className="field-label">
        What should GoalPulse watch for?
      </label>
      <textarea
        id="goal"
        rows={4}
        value={text}
        maxLength={MAX_CHARS}
        placeholder="Describe the product you want to buy, or the supplies you restock every month, with your budget."
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit();
        }}
        aria-describedby="goal-help"
      />
      <p id="goal-help" className="hint">
        GoalPulse asks a few questions, shows you a summary, and only starts watching after you confirm. It never buys
        anything for you.
        {API_MODE === "gateway" && (
          <>
            {" "}
            What you type here and in your answers is sent to Anthropic&apos;s Claude to understand your goal, so
            don&apos;t include personal or confidential details.
          </>
        )}
      </p>
      <div className="examples" aria-label="Examples">
        {EXAMPLES.map((ex) => (
          <button key={ex} type="button" className="chip" onClick={() => setText(ex)}>
            {ex}
          </button>
        ))}
      </div>
      {error && (
        <Notice tone="error">
          {error}
        </Notice>
      )}
      <div className="actions">
        <button type="submit" className="primary" disabled={!valid || busy}>
          {busy ? "Starting…" : "Start"}
        </button>
      </div>
    </form>
  );
}
