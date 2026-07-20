import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { loadHostState, subscribeHostState, updateHostCursor } from "./host";
import {
  activitiesAt,
  replayAt,
  sourceSequenceAt,
} from "./replay";
import type {
  CanvasHostState,
  CanvasState,
  PlanStep,
  Projection,
  Selection,
} from "./types";
import "./styles.css";

const STAGES = [
  ["catalog", "Catalog"],
  ["match", "Match"],
  ["plan", "Plan"],
  ["route", "Route"],
  ["evidence", "Evidence"],
  ["complete", "Complete"],
] as const;

const CONTEXT_LABELS: Record<string, string> = {
  work_iq: "Work IQ",
  foundry_iq: "Foundry IQ",
  fabric_iq: "Fabric IQ",
  web_iq: "Web IQ",
  attachment: "Attachment",
  prior_step: "Prior step",
};

function words(identifier: string): string[] {
  return identifier
    .replace(/^consumer-/, "")
    .replace(/^evidence-/, "")
    .split(/[-_.:]+/)
    .filter(Boolean);
}

function title(identifier: string): string {
  return words(identifier)
    .map((part) => {
      const lower = part.toLowerCase();
      if (lower === "rd") return "R&D";
      if (lower === "iq") return "IQ";
      if (lower === "a2a") return "A2A";
      if (lower === "eu") return "EU";
      return `${part.charAt(0).toUpperCase()}${part.slice(1)}`;
    })
    .join(" ");
}

function initials(identifier: string): string {
  return words(identifier)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

function formatDuration(durationMs: number | null): string {
  if (durationMs === null) return "In progress";
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(1)} s`;
}

function progressIndex(state: CanvasState): number {
  if (state.status !== "running") return 5;
  if (state.evidenceIds.length > 0) return 4;
  if (
    Object.keys(state.invocations).length > 0 ||
    Object.keys(state.approvals).length > 0 ||
    Object.keys(state.tasks).length > 0
  ) {
    return 3;
  }
  if (state.plan !== null) return 2;
  if (state.candidates.length > 0 || state.selections.length > 0) return 1;
  return state.candidateCount > 0 ? 0 : 0;
}

function agentStatus(state: CanvasState, agentId: string): string {
  const task = Object.values(state.tasks).find((item) => item.agentId === agentId);
  if (task !== undefined) return title(task.status);
  const invocations = Object.values(state.invocations).filter((item) => item.agentId === agentId);
  if (invocations.some((item) => item.status === "failed")) return "Needs attention";
  if (invocations.some((item) => item.status === "running")) return "Working";
  if (invocations.length > 0 && invocations.every((item) => item.status === "completed")) {
    return "Complete";
  }
  return "Ready";
}

function stepStatus(state: CanvasState, step: PlanStep): string {
  const invocation = Object.values(state.invocations).find((item) => item.stepId === step.stepId);
  if (invocation !== undefined) return invocation.status;
  if (step.invocationMode === "async") {
    const task = Object.values(state.tasks).find((item) => item.agentId === step.agentId);
    if (task !== undefined) return task.status;
  }
  const approval = Object.values(state.approvals).find((item) =>
    item.stepIds.includes(step.stepId),
  );
  if (approval?.status === "requested") return "awaiting approval";
  if (approval !== undefined && approval.status !== "approved") {
    return `approval ${approval.status}`;
  }
  return "queued";
}

function planLevels(steps: PlanStep[]): PlanStep[][] {
  const levels = new Map<string, number>();
  const unresolved = new Map(steps.map((step) => [step.stepId, step]));
  while (unresolved.size > 0) {
    let changed = false;
    for (const [stepId, step] of unresolved) {
      if (step.dependsOn.every((dependency) => levels.has(dependency))) {
        levels.set(
          stepId,
          step.dependsOn.length === 0
            ? 0
            : Math.max(...step.dependsOn.map((dependency) => levels.get(dependency) ?? 0)) + 1,
        );
        unresolved.delete(stepId);
        changed = true;
      }
    }
    if (!changed) {
      for (const stepId of unresolved.keys()) levels.set(stepId, 0);
      break;
    }
  }
  const maximum = Math.max(0, ...levels.values());
  return Array.from({ length: maximum + 1 }, (_, level) =>
    steps.filter((step) => levels.get(step.stepId) === level),
  );
}

function useReducedMotion(): boolean {
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(preference.matches);
    update();
    preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);

  return reducedMotion;
}

interface ReplayControlsProps {
  projection: Projection;
  cursor: number;
  onCursorChange: (cursor: number) => void;
}

function ReplayControls({ projection, cursor, onCursorChange }: ReplayControlsProps) {
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | null>(null);
  const sourceSequence = sourceSequenceAt(projection, cursor);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (!playing || reducedMotion) return undefined;
    timer.current = window.setInterval(() => {
      onCursorChange(Math.min(cursor + 1, projection.events.length - 1));
    }, 420);
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current);
    };
  }, [cursor, onCursorChange, playing, projection.events.length, reducedMotion]);

  useEffect(() => {
    if (cursor >= projection.events.length - 1) setPlaying(false);
  }, [cursor, projection.events.length]);

  const toggle = () => {
    if (reducedMotion) {
      onCursorChange(cursor >= projection.events.length - 1 ? 0 : cursor + 1);
      return;
    }
    if (cursor >= projection.events.length - 1) onCursorChange(0);
    setPlaying((current) => !current);
  };

  const buttonLabel = reducedMotion
    ? cursor >= projection.events.length - 1
      ? "Restart replay"
      : "Next event"
    : playing
      ? "Pause"
      : cursor >= projection.events.length - 1
        ? "Replay"
        : "Play";

  return (
    <section className="replay-controls" aria-label="Execution replay controls">
      <button className="replay-button" type="button" onClick={toggle}>
        <span aria-hidden="true">{reducedMotion ? "→" : playing ? "Ⅱ" : "▶"}</span>
        {buttonLabel}
      </button>
      <label className="replay-range">
        <span>Replay position</span>
        <input
          type="range"
          min={0}
          max={projection.events.length - 1}
          value={cursor}
          onChange={(event) => onCursorChange(Number(event.target.value))}
        />
      </label>
      <span className="replay-count" aria-live="polite">
        Event {sourceSequence + 1} / {projection.canonicalEventCount}
      </span>
    </section>
  );
}

function StageRail({ state }: { state: CanvasState }) {
  const current = progressIndex(state);
  return (
    <ol className="stage-rail" aria-label="Agent IQ lifecycle">
      {STAGES.map(([key, label], index) => (
        <li
          key={key}
          className={index < current ? "done" : index === current ? "active" : "waiting"}
          aria-current={index === current ? "step" : undefined}
        >
          <span className="stage-dot" aria-hidden="true">
            {index < current ? "✓" : index + 1}
          </span>
          <span>{label}</span>
        </li>
      ))}
    </ol>
  );
}

function CandidateScan({ state }: { state: CanvasState }) {
  const selectedIds = new Set(state.selections.map((selection) => selection.agentId));
  if (state.candidates.length === 0) {
    return (
      <div className="empty-state">
        Waiting for eligible catalog candidates. No selection has been inferred.
      </div>
    );
  }
  return (
    <div
      className="candidate-strip"
      role="list"
      aria-label="Ranked eligible candidates"
      tabIndex={0}
    >
      {state.candidates.map((candidate) => (
        <article
          role="listitem"
          key={candidate.agentId}
          className={`candidate-pill ${selectedIds.has(candidate.agentId) ? "selected" : ""}`}
        >
          <span className="candidate-rank">#{candidate.rank}</span>
          <span className="candidate-name">{title(candidate.agentId)}</span>
          <span className="candidate-score">RRF {candidate.fusedScore.toFixed(4)}</span>
        </article>
      ))}
    </div>
  );
}

function AgentCard({
  selection,
  state,
  index,
}: {
  selection: Selection;
  state: CanvasState;
  index: number;
}) {
  const status = agentStatus(state, selection.agentId);
  return (
    <article className="agent-card" style={{ "--agent-index": index } as CSSProperties}>
      <div className="agent-card-head">
        <div className="portrait" aria-hidden="true">
          <span>{initials(selection.agentId)}</span>
        </div>
        <div>
          <h3>{title(selection.agentId)}</h3>
          <p>{selection.summary}</p>
        </div>
        <span className={`agent-status status-${status.toLowerCase().replaceAll(" ", "-")}`}>
          {status}
        </span>
      </div>
      <div className="signal-row" aria-label="Public match signals">
        {selection.matchedTags.slice(0, 3).map((tag) => (
          <span key={tag}>{tag}</span>
        ))}
      </div>
      <footer>
        <span>RRF {selection.fusedScore.toFixed(4)}</span>
        <span>{selection.policyFacts.length} policy facts</span>
      </footer>
    </article>
  );
}

function Squad({ state }: { state: CanvasState }) {
  return (
    <section className="panel squad-panel" aria-labelledby="squad-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Selected formation</p>
          <h2 id="squad-heading">
            {state.selections.length > 0
              ? `${state.selections.length} specialists, one governed team`
              : "Formation pending"}
          </h2>
        </div>
        <span className="quiet-badge">Public signals only</span>
      </div>
      <div className="squad-grid">
        {state.selections.map((selection, index) => (
          <AgentCard
            key={selection.agentId}
            selection={selection}
            state={state}
            index={index}
          />
        ))}
      </div>
    </section>
  );
}

function PlanGraph({ state }: { state: CanvasState }) {
  const plan = state.plan;
  if (plan === null) {
    return (
      <section className="panel" aria-labelledby="plan-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Execution DAG</p>
            <h2 id="plan-heading">Plan pending</h2>
          </div>
        </div>
        <div className="empty-state">No plan facts have been projected yet.</div>
      </section>
    );
  }
  const levels = planLevels(plan.steps);
  return (
    <section className="panel" aria-labelledby="plan-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Execution DAG</p>
          <h2 id="plan-heading">
            {plan.steps.length} steps · up to {plan.maxConcurrency} in parallel
          </h2>
        </div>
        <span className="quiet-badge">{levels.length} phases</span>
      </div>
      <div className="dag" role="list" aria-label="Execution plan phases">
        {levels.map((steps, levelIndex) => (
          <div className="dag-level" role="listitem" key={`level-${levelIndex}`}>
            <div className="dag-phase">
              <span>Phase {levelIndex + 1}</span>
              <small>{steps.length > 1 ? "parallel" : "sequential"}</small>
            </div>
            <div className="dag-nodes">
              {steps.map((step) => (
                <article className="dag-node" key={step.stepId}>
                  <header>
                    <span className="dag-monogram" aria-hidden="true">
                      {initials(step.agentId)}
                    </span>
                    <div>
                      <strong>{title(step.agentId)}</strong>
                      <span>{title(step.stepId)}</span>
                    </div>
                    <span className={`node-status node-${stepStatus(state, step).replaceAll(" ", "-")}`}>
                      {title(stepStatus(state, step))}
                    </span>
                  </header>
                  <div className="context-row" aria-label="Approved context kinds">
                    {step.contextKinds.map((kind) => (
                      <span key={kind}>{CONTEXT_LABELS[kind] ?? title(kind)}</span>
                    ))}
                    {step.approvalRequired && <span className="approval-chip">Approval</span>}
                  </div>
                  {step.dependsOn.length > 0 && (
                    <p className="dependency">
                      After {step.dependsOn.map((dependency) => title(dependency)).join(", ")}
                    </p>
                  )}
                </article>
              ))}
            </div>
            {levelIndex < levels.length - 1 && <div className="dag-connector" aria-hidden="true" />}
          </div>
        ))}
      </div>
    </section>
  );
}

function EvidencePanel({ state }: { state: CanvasState }) {
  return (
    <section className="panel evidence-panel" aria-labelledby="evidence-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Provenance rail</p>
          <h2 id="evidence-heading">{state.evidenceIds.length} governed evidence objects</h2>
        </div>
        <span className="quiet-badge">Invocation-bound</span>
      </div>
      {state.evidenceIds.length === 0 ? (
        <div className="empty-state">Evidence appears only after a recorded evidence event.</div>
      ) : (
        <ol className="evidence-list">
          {state.evidenceIds.map((evidenceId, index) => (
            <li key={evidenceId}>
              <span className="citation-number">{state.citationNumbers[index] ?? index + 1}</span>
              <div>
                <strong>{title(evidenceId)}</strong>
                <span>{evidenceId}</span>
              </div>
              <span className="verified-mark">verified</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function DecisionPanel({ state }: { state: CanvasState }) {
  const approvals = Object.values(state.approvals);
  const tasks = Object.values(state.tasks);
  const failures = Object.values(state.invocations).filter(
    (invocation) => invocation.status === "failed",
  );
  return (
    <section className="panel decision-panel" aria-labelledby="decision-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Decision and handoff</p>
          <h2 id="decision-heading">Governed boundaries</h2>
        </div>
      </div>
      <div className="decision-stack">
        {approvals.map((approval) => (
          <article className="decision-card" key={approval.approvalId}>
            <span className="decision-icon" aria-hidden="true">
              ◆
            </span>
            <div>
              <strong>{title(approval.status)} approval</strong>
              <p>{approval.scopeSummary}</p>
            </div>
          </article>
        ))}
        {tasks.map((task) => (
          <article className="decision-card" key={task.taskId}>
            <span className="decision-icon task-icon" aria-hidden="true">
              ↗
            </span>
            <div>
              <strong>{title(task.status)} durable task</strong>
              <p>
                {title(task.agentId)} · callback via{" "}
                {task.callbackModes.map((mode) => title(mode)).join(" and ")}
              </p>
            </div>
          </article>
        ))}
        {failures.map((failure) => (
          <article className="decision-card failure-card" key={failure.invocationId}>
            <span className="decision-icon" aria-hidden="true">
              !
            </span>
            <div>
              <strong>{title(failure.agentId)} did not complete</strong>
              <p>Typed failure: {failure.errorCode ?? "invocation_failed"}</p>
            </div>
          </article>
        ))}
        {approvals.length === 0 && tasks.length === 0 && failures.length === 0 && (
          <div className="empty-state">No approval, durable task, or failure has been recorded.</div>
        )}
      </div>
    </section>
  );
}

function Timeline({
  projection,
  cursor,
}: {
  projection: Projection;
  cursor: number;
}) {
  const activities = activitiesAt(projection, cursor);
  const firstTimestamp = activities[0]?.timestamp ?? 0;
  return (
    <section className="panel timeline-panel" aria-labelledby="timeline-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Replayable activity</p>
          <h2 id="timeline-heading">{activities.length} proven lifecycle records</h2>
        </div>
        <span className="quiet-badge">No hidden reasoning</span>
      </div>
      <ol className="timeline" aria-label="Recorded lifecycle activity" tabIndex={0}>
        {activities.map((activity) => (
          <li key={activity.id}>
            <span className={`timeline-marker marker-${activity.status}`} aria-hidden="true" />
            <div>
              <strong>{activity.label}</strong>
              <span>
                +{((activity.timestamp - firstTimestamp) / 1000).toFixed(1)} s
                {activity.agentId ? ` · ${title(activity.agentId)}` : ""}
              </span>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function AnswerPanel({ state }: { state: CanvasState }) {
  return (
    <section className="answer-panel" aria-labelledby="answer-heading">
      <div className="answer-mark" aria-hidden="true">
        A
      </div>
      <div>
        <p className="eyebrow">Grounded decision</p>
        <h2 id="answer-heading">
          {state.answer === null ? "The cited answer is not available yet" : "Conditional go"}
        </h2>
        <p className="answer-copy">
          {state.answer ?? "Agent IQ will not render an answer before synthesis is recorded."}
        </p>
        <footer>
          <span>{title(state.status)}</span>
          <span>{formatDuration(state.durationMs)}</span>
          <span>{state.citationNumbers.length} citations</span>
        </footer>
      </div>
    </section>
  );
}

interface CommandCenterProps {
  hostState: CanvasHostState;
  cursor: number;
  onCursorChange: (cursor: number) => void;
}

export function CommandCenter({ hostState, cursor, onCursorChange }: CommandCenterProps) {
  const { projection } = hostState;
  const state = useMemo(() => replayAt(projection, cursor), [cursor, projection]);
  const currentStage = STAGES[progressIndex(state)]?.[1] ?? "Catalog";
  return (
    <main className="command-center">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <span>A</span>
          </div>
          <div>
            <strong>Agent IQ</strong>
            <span>Command Center</span>
          </div>
        </div>
        <div className="run-meta">
          <span className={`run-status run-${state.status}`}>
            <span aria-hidden="true" />
            {title(state.status)}
          </span>
          <span>{hostState.mode === "demo" ? "Certified replay" : "Authorized live run"}</span>
          <span className="execution-id">{state.executionId}</span>
        </div>
      </header>

      <div className="canvas-shell">
        <section className="hero" aria-labelledby="hero-heading">
          <div className="hero-copy">
            <p className="eyebrow">One intent · the right AI team · every step proven</p>
            <h1 id="hero-heading">EU Cold-Water Launch Command Center</h1>
            <p>
              Agent IQ scanned {state.candidateCount || "the"} eligible catalog
              {state.candidateCount === 1 ? " candidate" : " candidates"} and formed a governed{" "}
              {state.selections.length || "pending"}-specialist squad.
            </p>
          </div>
          <div className="hero-stage" aria-live="polite">
            <span>Current stage</span>
            <strong>{currentStage}</strong>
          </div>
        </section>

        <StageRail state={state} />
        <ReplayControls
          projection={projection}
          cursor={cursor}
          onCursorChange={onCursorChange}
        />

        <section className="scan-panel" aria-labelledby="scan-heading">
          <div className="section-heading compact-heading">
            <div>
              <p className="eyebrow">Authorized catalog scan</p>
              <h2 id="scan-heading">Ranked candidate window</h2>
            </div>
            <span className="quiet-badge">RRF · not calibrated confidence</span>
          </div>
          <CandidateScan state={state} />
        </section>

        <Squad state={state} />
        <div className="two-column">
          <PlanGraph state={state} />
          <Timeline projection={projection} cursor={cursor} />
        </div>
        <div className="two-column lower-grid">
          <EvidencePanel state={state} />
          <DecisionPanel state={state} />
        </div>
        <AnswerPanel state={state} />

        <footer className="canvas-footer">
          <span>{hostState.source}</span>
          <span>Projection v{projection.schemaVersion}</span>
          <span>
            Canonical {sourceSequenceAt(projection, cursor) + 1} /{" "}
            {projection.canonicalEventCount}
          </span>
        </footer>
      </div>
    </main>
  );
}

export default function App() {
  const [hostState, setHostState] = useState<CanvasHostState | null>(null);
  const [cursor, setCursor] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void loadHostState()
      .then((loaded) => {
        if (!active) return;
        setHostState(loaded);
        setCursor(
          loaded.cursor >= 0
            ? Math.min(loaded.cursor, loaded.projection.events.length - 1)
            : loaded.projection.events.length - 1,
        );
      })
      .catch(() => {
        if (active) setError("The governed Canvas replay could not be loaded.");
      });
    const unsubscribe = subscribeHostState(
      (next) => {
        if (!active) return;
        setHostState(next);
        setCursor(
          next.cursor >= 0
            ? Math.min(next.cursor, next.projection.events.length - 1)
            : next.projection.events.length - 1,
        );
      },
      () => {
        if (active) setError("The live Canvas subscription was interrupted.");
      },
    );
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  const handleCursorChange = (nextCursor: number) => {
    setCursor(nextCursor);
    void updateHostCursor(nextCursor).catch(() => {
      setError("Replay remains available locally, but the shared cursor was not synchronized.");
    });
  };

  if (error !== null && hostState === null) {
    return (
      <main className="load-state" role="alert">
        <div className="brand-mark" aria-hidden="true">
          <span>A</span>
        </div>
        <h1>Canvas unavailable</h1>
        <p>{error}</p>
      </main>
    );
  }
  if (hostState === null) {
    return (
      <main className="load-state" aria-busy="true">
        <div className="brand-mark pulse" aria-hidden="true">
          <span>A</span>
        </div>
        <h1>Loading governed replay</h1>
        <p>Waiting for a content-safe AG-UI projection.</p>
      </main>
    );
  }
  return (
    <>
      {error !== null && (
        <div className="connection-notice" role="status">
          {error}
        </div>
      )}
      <CommandCenter
        hostState={hostState}
        cursor={cursor}
        onCursorChange={handleCursorChange}
      />
    </>
  );
}
