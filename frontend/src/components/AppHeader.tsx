"use client";

import Link from "next/link";
import { useState, useSyncExternalStore } from "react";

import { API_MODE, GATEWAY_URL } from "@/lib/api";
import { getToken, onTokenChange, setToken } from "@/lib/auth";

function useToken(): string | null {
  return useSyncExternalStore(onTokenChange, getToken, () => null);
}

export function AppHeader() {
  return (
    <header className="app-header">
      <Link href="/" className="brand">
        GoalPulse
      </Link>
      {API_MODE === "mock" ? (
        <span className="badge badge-mock" title="Responses are recorded from the Goal Intelligence service; your text is not interpreted.">
          Mock mode · scripted replies
        </span>
      ) : (
        <GatewayAuth />
      )}
    </header>
  );
}

function GatewayAuth() {
  const token = useToken();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  if (token && !editing) {
    return (
      <div className="auth">
        <span className="badge" title={GATEWAY_URL}>
          Signed in
        </span>
        <button type="button" className="link-button" onClick={() => setToken(null)}>
          Sign out
        </button>
      </div>
    );
  }

  return (
    <form
      className="auth"
      onSubmit={(e) => {
        e.preventDefault();
        setToken(draft);
        setDraft("");
        setEditing(false);
      }}
    >
      {/* Interim until ICP-08: paste a token from the agreed dev issuer. */}
      <label htmlFor="token" className="visually-hidden">
        Access token
      </label>
      <input
        id="token"
        type="password"
        autoComplete="off"
        placeholder="Paste dev access token"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <button type="submit" disabled={!draft.trim()}>
        Sign in
      </button>
    </form>
  );
}
