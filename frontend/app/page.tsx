"use client";

import { useEffect, useRef, useState } from "react";

type Verdict = "verified" | "incomplete" | "rejected" | "timeout" | "unverified" | "disabled";

type Session = {
  id: string;
  name: string;
  binders: string;
  statement: string;
  signature: string;
  verification_enabled: boolean;
};

type CheckResult = {
  node_id: string;
  ok: boolean;
  status: Verdict;
  diagnostics: string;
  lean_version: string | null;
  axioms: Record<string, string[]>;
};

type TreeNode = {
  id: string;
  parent_id: string | null;
  kind: string;
  label: string;
  status: string;
  payload: Record<string, unknown>;
};

type Suggestion = { tactic: string; confidence: number; explanation: string };

type Counterexample = { supported: boolean; found: boolean; explanation: string };

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const START = { title: "Playground proof", statement: "n + 0 = n", binders: "(n : Nat)" };
const STARTER_PROOF = "simp";

// Every verdict is said in words, not only by colour.
const VERDICTS: Record<Verdict, { label: string; hint: string }> = {
  verified: { label: "Verified", hint: "Lean accepted this proof, and it depends only on standard axioms." },
  incomplete: { label: "Incomplete", hint: "Lean accepted the file, but it is not a complete proof (for example, it uses sorry)." },
  rejected: { label: "Rejected", hint: "Lean found errors, or the submission was refused. Nothing was proved." },
  timeout: { label: "Timed out", hint: "Lean did not finish in time. Nothing was proved." },
  unverified: { label: "Not checked", hint: "Lean is not installed on the server, so nothing was checked." },
  disabled: { label: "Checking turned off", hint: "This server does not run Lean on submitted text, so nothing was checked." },
};

const NODE_STATUS: Record<string, string> = {
  active: "Goal",
  complete: "Verified",
  error: "Not accepted",
  pending: "Not checked",
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { ...init, headers: { "Content-Type": "application/json" } });
  const body: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") throw new Error(detail);
    if (Array.isArray(detail)) {
      throw new Error(detail.map((item) => (item as { msg?: string }).msg ?? "invalid input").join("; "));
    }
    throw new Error(`The server returned status ${response.status}.`);
  }
  return body as T;
}

function errorMessage(error: unknown): string {
  if (error instanceof TypeError) {
    return `Cannot reach the AutoProof API at ${API}. Start the backend, then reload.`;
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

function indent(proof: string): string {
  return proof
    .split("\n")
    .map((line) => (line.trim() ? `  ${line.trimEnd()}` : ""))
    .join("\n");
}

function proofLabel(proof: string): string {
  const first = proof.split("\n").find((line) => line.trim());
  return (first ?? "proof").trim().slice(0, 200) || "proof";
}

function toPercent(value: number): number {
  return Math.round(value <= 1 ? value * 100 : value);
}

export default function Home() {
  const [session, setSession] = useState<Session | null>(null);
  const [apiProblem, setApiProblem] = useState<string | null>(null);
  const [proof, setProof] = useState(STARTER_PROOF);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [suggestionSource, setSuggestionSource] = useState("");
  const [result, setResult] = useState<CheckResult | null>(null);
  const [resultFor, setResultFor] = useState("");
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [lastLabel, setLastLabel] = useState("");
  const [counter, setCounter] = useState<string | null>(null);
  const [notice, setNotice] = useState("Starting a proof session…");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const started = useRef(false);

  const signature = session?.signature ?? "";
  const leanSource = signature ? `${signature} := by\n${indent(proof)}\n` : "";

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const created = await request<Session>("/api/sessions", { method: "POST", body: JSON.stringify(START) });
        setSession(created);
        const [treeBody, suggested] = await Promise.all([
          request<{ nodes: TreeNode[] }>(`/api/sessions/${created.id}/tree`),
          request<{ tactics: Suggestion[]; source: string }>("/api/suggest", {
            method: "POST",
            body: JSON.stringify({ session_id: created.id }),
          }),
        ]);
        setTree(treeBody.nodes);
        setSuggestions(suggested.tactics);
        setSuggestionSource(suggested.source);
        setNotice("Ready. Write tactics below and run them through Lean.");
      } catch (error) {
        setApiProblem(errorMessage(error));
        setNotice("The proof service is unavailable.");
      }
    })();
  }, []);

  async function refreshTree(sessionId: string) {
    const body = await request<{ nodes: TreeNode[] }>(`/api/sessions/${sessionId}/tree`);
    setTree(body.nodes);
  }

  async function runProof() {
    if (!session) return;
    if (!proof.trim()) {
      setNotice("Write a tactic proof first. The statement is fixed; you only write the tactics.");
      return;
    }
    const label = proofLabel(proof);
    const submitted = proof;
    setBusy(true);
    setNotice("Checking with Lean…");
    try {
      const checked = await request<CheckResult>("/api/apply-tactic", {
        method: "POST",
        body: JSON.stringify({ session_id: session.id, tactic: label, proof: submitted }),
      });
      setResult(checked);
      setResultFor(submitted);
      setLastLabel(label);
      setNotice(`${VERDICTS[checked.status].label}. ${VERDICTS[checked.status].hint}`);
      await refreshTree(session.id);
    } catch (error) {
      setNotice(`Could not check the proof: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  }

  function insertSuggestion(tactic: string) {
    setProof((current) => (current.trim() ? `${current.trimEnd()}\n${tactic}` : tactic));
    setNotice(`Added “${tactic}”. Review it, then run the proof.`);
  }

  async function refreshSuggestions() {
    if (!session) return;
    try {
      const body = await request<{ tactics: Suggestion[]; source: string }>("/api/suggest", {
        method: "POST",
        body: JSON.stringify({ session_id: session.id }),
      });
      setSuggestions(body.tactics);
      setSuggestionSource(body.source);
    } catch (error) {
      setNotice(`Could not load suggestions: ${errorMessage(error)}`);
    }
  }

  async function searchCounterexample() {
    if (!session) return;
    setCounter("Searching…");
    try {
      const body = await request<Counterexample>("/api/counterexample", {
        method: "POST",
        body: JSON.stringify({ session_id: session.id }),
      });
      setCounter(body.explanation);
    } catch (error) {
      setCounter(`Search failed: ${errorMessage(error)}`);
    }
  }

  async function sendFeedback(verdict: "approved" | "edited" | "rejected") {
    if (!session) return;
    try {
      const body = await request<{ message: string }>("/api/feedback", {
        method: "POST",
        body: JSON.stringify({
          session_id: session.id,
          tactic: lastLabel || proofLabel(proof),
          verdict,
          edited_tactic: verdict === "edited" ? proof : undefined,
          note: note.trim() || undefined,
        }),
      });
      setFeedback(body.message);
    } catch (error) {
      setFeedback(`Feedback not saved: ${errorMessage(error)}`);
    }
  }

  async function copyLean() {
    try {
      await navigator.clipboard.writeText(leanSource);
      setNotice("Copied the Lean source.");
    } catch {
      setNotice("Copying failed. Select the source and copy it manually.");
    }
  }

  const verdict = result ? VERDICTS[result.status] : null;
  const stale = result !== null && resultFor !== proof;

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="AutoProof home">
          <span className="brand-mark">∀</span>
          <span>Auto<span>Proof</span></span>
        </a>
        <div className="workspace-label"><i /> Playground <span>Lean 4</span></div>
        <nav>
          <button className="quiet" onClick={copyLean} disabled={!session}>Copy Lean</button>
        </nav>
      </header>

      <section className="intro">
        <div>
          <p className="eyebrow">INTERACTIVE THEOREM PROVING</p>
          <h1>Make every proof step<br /><em>checkable.</em></h1>
          <p className="lede">
            You write the tactics. The statement is fixed when the session starts, and Lean decides what was actually proved.
          </p>
        </div>
        <div className="progress" aria-live="polite">
          <div className="progress-row">
            <span>Verification</span>
            <strong>{session ? (session.verification_enabled ? "On" : "Off") : "…"}</strong>
          </div>
          <p>{session ? `Session ${session.id.slice(0, 8)}` : "No session yet"}</p>
        </div>
      </section>

      {apiProblem && <div className="notice-banner" role="alert">{apiProblem}</div>}
      {session && !session.verification_enabled && (
        <div className="notice-banner" role="note">
          Lean checking is turned off on this server, so no proof here can be marked verified. Operators can enable it on a
          trusted machine with AUTOPROOF_TRUSTED_LEAN=1.
        </div>
      )}

      <section className="workspace">
        <article className="panel editor-panel">
          <div className="panel-head">
            <div><span className="file-dot" /> <b>main.lean</b><small>Lean 4</small></div>
            <button className="run" onClick={runProof} disabled={busy || !session}>
              <span>▶</span> {busy ? "Checking…" : "Run proof"}
            </button>
          </div>
          <pre className="signature" aria-label="Fixed statement">{signature ? `${signature} := by` : "Loading statement…"}</pre>
          <textarea
            className="proof-input"
            aria-label="Tactic proof"
            value={proof}
            spellCheck={false}
            rows={10}
            onChange={(event) => setProof(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault();
                void runProof();
              }
            }}
          />
          <p className="hint-line">Write tactics only; the statement above is fixed. Press Ctrl or ⌘ + Enter to run.</p>
          <div className="editor-status" role="status"><span className="status-dot" /> {notice}</div>

          {result && verdict && (
            <div className={`result result-${result.status}`}>
              <p className="verdict"><strong>{verdict.label}.</strong> {verdict.hint}</p>
              {stale && <p className="stale">The proof has changed since this check. Run it again to update the verdict.</p>}
              {Object.keys(result.axioms).length > 0 && (
                <p className="axioms">
                  Axioms: {Object.entries(result.axioms).map(([name, list]) => `${name}: ${list.length ? list.join(", ") : "none"}`).join(" · ")}
                </p>
              )}
              {result.lean_version && <p className="axioms">Checked with {result.lean_version}</p>}
              {result.diagnostics && <pre className="diagnostics">{result.diagnostics}</pre>}
            </div>
          )}
        </article>

        <aside className="right-rail">
          <section className="panel goal-card">
            <div className="card-label">CURRENT GOAL</div>
            <code>{signature || "…"}</code>
            <p>The statement is set when the session starts and cannot be changed afterwards.</p>
          </section>
          <section className="panel suggestion-card">
            <div className="card-label">
              SUGGESTIONS
              <button onClick={refreshSuggestions} aria-label="Refresh suggestions" disabled={!session}>↻</button>
            </div>
            {suggestions.length === 0 && <p className="muted">No suggestions yet.</p>}
            {suggestions.map((item, index) => (
              <button className="suggestion" key={`${item.tactic}-${index}`} onClick={() => insertSuggestion(item.tactic)}>
                <span className="tactic">{item.tactic}</span>
                <span className="confidence">{toPercent(item.confidence)}%</span>
                <span className="hint">{item.explanation}</span>
                <span className="insert">Add →</span>
              </button>
            ))}
            {suggestionSource && (
              <p className="muted small">Source: {suggestionSource}. Suggestions are advisory; only Lean can verify a proof.</p>
            )}
          </section>
        </aside>
      </section>

      <section className="lower-grid">
        <article className="panel proof-panel">
          <div className="section-title">
            <div><p className="eyebrow">PROOF TREE</p><h2>Attempts so far</h2></div>
          </div>
          <ol className="tree-list">
            {tree.map((node) => (
              <li key={node.id} className={`tree-node ${node.status}`}>
                <span className="node-icon">{node.kind === "goal" ? "⊢" : "▸"}</span>
                <div>
                  <b>{node.label}</b>
                  <small>{NODE_STATUS[node.status] ?? node.status}</small>
                </div>
              </li>
            ))}
          </ol>
          {tree.length <= 1 && <p className="muted">Run a proof to add attempts here.</p>}
        </article>

        <article className="panel feedback-panel">
          <p className="eyebrow">FEEDBACK</p>
          <h2>Record your judgment</h2>
          <p>Feedback is saved with this session. It does not yet change the suggestions.</p>
          <label className="note-label">
            Note (optional)
            <input value={note} maxLength={4000} onChange={(event) => setNote(event.target.value)} />
          </label>
          <div className="feedback-actions">
            <button onClick={() => sendFeedback("approved")} disabled={!session}>✓ Approve</button>
            <button onClick={() => sendFeedback("edited")} disabled={!session}>✎ Save my edit</button>
            <button onClick={() => sendFeedback("rejected")} disabled={!session}>× Reject</button>
          </div>
          {feedback && <p role="status" className="feedback-msg">{feedback}</p>}
        </article>

        <article className="panel counter-panel">
          <p className="eyebrow">COUNTEREXAMPLE SEARCH</p>
          <h2>Test the claim</h2>
          <p>Checks natural-number values from 0 to 12. Finding nothing is evidence, not a proof.</p>
          <button onClick={searchCounterexample} disabled={!session}>Search values 0–12 <span>→</span></button>
          {counter && <div className="counter-result" role="status">{counter}</div>}
        </article>
      </section>

      <footer>
        <span>AutoProof <b>0.2.0</b></span>
        <span>Human-guided formal reasoning</span>
        <a href="https://lean-lang.org" target="_blank" rel="noreferrer">Learn Lean ↗</a>
      </footer>
    </main>
  );
}
