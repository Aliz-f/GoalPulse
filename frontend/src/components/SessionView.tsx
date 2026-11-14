"use client";

import { Send, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Icon, Notice } from "@/components/Icons";
import { DraftSummary } from "@/components/DraftSummary";
import { getApi, sessionCache, type Turn } from "@/lib/api";
import { adviceFor } from "@/lib/api/errors";
import { hasMockControls } from "@/lib/api/types";
import type { GoalDraft, GoalSpec } from "@/lib/contracts";
import { formatTimestamp } from "@/lib/format";
import { isUnsupported, labelForField, summarizeCandidate } from "@/lib/summary";

type Busy = null | "load" | "send" | "confirm";
type NoticeState = { kind: "error" | "conflict"; text: string } | null;

const MAX_CHARS = 2000;

export function SessionView() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const api = getApi();
  const cached = sessionCache.get(sessionId);

  const [draft, setDraft] = useState<GoalDraft | null>(cached?.draft ?? null);
  const [turns, setTurns] = useState<Turn[]>(cached?.turns ?? []);
  const [busy, setBusy] = useState<Busy>(cached ? null : "load");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeState>(null);
  const [text, setText] = useState("");
  const [goal, setGoal] = useState<GoalSpec | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const remember = useCallback(
    (d: GoalDraft, t: Turn[]) => {
      setDraft(d);
      setTurns(t);
      sessionCache.set(sessionId, { draft: d, turns: t });
    },
    [sessionId],
  );

  /** Re-read the server's current revision (after a 409 or on first load). */
  const reload = useCallback(
    async (prevTurns: Turn[]) => {
      const fresh = await api.getSession(sessionId);
      const last = prevTurns.at(-1);
      const t =
        fresh.next_question && !(last?.role === "assistant" && last.text === fresh.next_question)
          ? [...prevTurns, { role: "assistant" as const, text: fresh.next_question }]
          : prevTurns;
      remember(fresh, t);
      return fresh;
    },
    [api, sessionId, remember],
  );

  useEffect(() => {
    if (cached) return;
    let cancelled = false;
    api.getSession(sessionId).then(
      (fresh) => {
        if (cancelled) return;
        remember(fresh, fresh.next_question ? [{ role: "assistant", text: fresh.next_question }] : []);
        setBusy(null);
      },
      (err) => {
        if (cancelled) return;
        setLoadError(adviceFor(err, "load").message);
        setBusy(null);
      },
    );
    return () => {
      cancelled = true;
    };
    // load once per session id; `cached` is read from the module cache at mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  async function send() {
    const message = text.trim();
    if (!draft || !message || busy) return;
    setBusy("send");
    setNotice(null);
    try {
      const next = await api.sendMessage(sessionId, message, draft.revision);
      const t: Turn[] = [...turns, { role: "user", text: message }];
      if (next.next_question) t.push({ role: "assistant", text: next.next_question });
      remember(next, t);
      setText("");
    } catch (err) {
      const advice = adviceFor(err, "send");
      setNotice({ kind: advice.reload ? "conflict" : "error", text: advice.message });
      if (!advice.keepInput) setText("");
      if (advice.reload) await reload(turns).catch(() => undefined);
    } finally {
      setBusy(null);
      inputRef.current?.focus();
    }
  }

  async function confirm() {
    if (!draft?.ready_for_confirmation || busy) return;
    setBusy("confirm");
    setNotice(null);
    try {
      const spec = await api.confirm(sessionId, draft.revision);
      remember({ ...draft, state: "confirmed", ready_for_confirmation: false }, turns);
      setGoal(spec);
    } catch (err) {
      const advice = adviceFor(err, "confirm");
      setNotice({ kind: advice.reload ? "conflict" : "error", text: advice.message });
      if (advice.reload) await reload(turns).catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  if (loadError) {
    return (
      <div className="card">
        <Notice tone="error">
          {loadError}
        </Notice>
        <Link href="/">Start a new goal</Link>
      </div>
    );
  }
  if (!draft) return <p className="hint">Loading your draft…</p>;
  if (goal) return <Confirmed goal={goal} />;

  const summary = summarizeCandidate(draft.candidate_goal);
  const unsupported = isUnsupported(draft.missing_fields);
  const closed = draft.state === "confirmed" || draft.state === "abandoned";
  const ready = draft.ready_for_confirmation && draft.state === "ready_for_confirmation" && !unsupported;
  const stillNeeded = draft.missing_fields.filter((f) => f !== "summary_approval");
  const shownTurns: Turn[] =
    turns.length === 0 && draft.next_question ? [{ role: "assistant", text: draft.next_question }] : turns;
  const suggestion = hasMockControls(api) && !closed ? api.suggestedReply(sessionId) : null;

  return (
    <div className="session">
      <section className="conversation" aria-label="Conversation">
        <ol className="turns" aria-live="polite">
          {shownTurns.map((t, i) => (
            <li key={i} className={`turn turn-${t.role}`}>
              <span className="visually-hidden">{t.role === "user" ? "You:" : "GoalPulse:"}</span>
              {t.text}
            </li>
          ))}
        </ol>

        {unsupported && (
          <Notice tone="info">
            GoalPulse can&apos;t monitor this kind of goal yet. It works for a product you want to buy, or monthly supplies
            for a business.
          </Notice>
        )}
        {stillNeeded.length > 0 && !unsupported && (
          <p className="hint">Still needed: {stillNeeded.map(labelForField).join(", ")}</p>
        )}
        {notice && (
          <Notice tone={notice.kind === "conflict" ? "warning" : "error"}>
            {notice.text}
          </Notice>
        )}

        {closed ? (
          <Notice tone="info">This draft is {draft.state} and can&apos;t be changed.</Notice>
        ) : (
          <>
            {ready && (
              <div className="review" aria-label="Review and confirm">
                <h2>Review and confirm</h2>
                <p>
                  Monitoring starts only for exactly what&apos;s in the summary (revision {draft.revision}). GoalPulse
                  will notify you about verified offers and prepare the details — it never places an order.
                </p>
                <button
                  type="button"
                  className="primary with-icon"
                  onClick={() => void confirm()}
                  disabled={busy !== null}
                >
                  <Icon icon={ShieldCheck} />
                  {busy === "confirm" ? "Confirming…" : `Confirm revision ${draft.revision} and start monitoring`}
                </button>
              </div>
            )}
            <form
              className="reply"
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <label htmlFor="reply" className="field-label">
                {ready ? "Want to change something?" : "Your answer"}
              </label>
              <textarea
                id="reply"
                ref={inputRef}
                rows={2}
                value={text}
                maxLength={MAX_CHARS}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                disabled={busy === "confirm"}
              />
              <div className="actions">
                {suggestion && (
                  <button type="button" className="chip" onClick={() => setText(suggestion)}>
                    Scripted reply: {suggestion}
                  </button>
                )}
                <button type="submit" className="with-icon" disabled={!text.trim() || busy !== null}>
                  <Icon icon={Send} />
                  {busy === "send" ? "Sending…" : "Send"}
                </button>
              </div>
            </form>
            {hasMockControls(api) && (
              <button
                type="button"
                className="link-button"
                onClick={() => api.simulateConcurrentEdit(sessionId)}
                title="Mock only: the next send or confirm from this tab will get HTTP 409"
              >
                Simulate an edit from another tab
              </button>
            )}
          </>
        )}
      </section>

      <DraftSummary summary={summary} badge={`Revision ${draft.revision}`} />
    </div>
  );
}

function Confirmed({ goal }: { goal: GoalSpec }) {
  return (
    <section className="card confirmed" aria-live="polite">
      <h1>Monitoring is active</h1>
      <p>
        <strong>{goal.title}</strong> (version {goal.version}) was approved on {formatTimestamp(goal.approved_at)}.
      </p>
      <p className="hint">
        GoalPulse will check offers and tell you when one is verified. Nothing has been bought — when an offer is
        found you&apos;ll get the details to complete the purchase yourself.
      </p>
      <p className="actions actions-start">
        <Link href={`/goals/${goal.goal_id}`}>View this goal</Link>
        <Link href="/goals">My goals</Link>
        <Link href="/">Start another goal</Link>
      </p>
    </section>
  );
}
