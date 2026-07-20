export type RunStatus =
  | "running"
  | "completed"
  | "partial"
  | "declined"
  | "failed"
  | "cancelled";

export interface ScoreComponent {
  name: string;
  rank: number;
  score: number;
  matchedFields: string[];
}

export interface Candidate {
  agentId: string;
  rank: number;
  fusedScore: number;
  explanation: string;
  matchedFields: string[];
  components: ScoreComponent[];
}

export interface Selection {
  agentId: string;
  summary: string;
  matchedSkills: string[];
  matchedTags: string[];
  policyFacts: string[];
  fusedScore: number;
}

export interface PlanStep {
  stepId: string;
  agentId: string;
  dependsOn: string[];
  contextKinds: string[];
  expectedOutputCount: number;
  invocationMode: string;
  approvalRequired: boolean;
}

export interface Plan {
  planId: string;
  steps: PlanStep[];
  maxConcurrency: number;
}

export interface Invocation {
  invocationId: string;
  stepId: string;
  agentId: string;
  status: "running" | "completed" | "failed";
  transport: string;
  protocolVersion: string;
  durationMs: number | null;
  evidenceIds: string[];
  errorCode: string | null;
}

export interface Approval {
  approvalId: string;
  stepIds: string[];
  scopeSummary: string;
  status: "requested" | "approved" | "declined" | "expired" | "cancelled";
}

export interface DurableTask {
  taskId: string;
  agentId: string;
  callbackModes: string[];
  status: string;
}

export interface CanvasState {
  executionId: string;
  status: RunStatus;
  stage: string;
  catalogVersion: string | null;
  candidateCount: number;
  candidates: Candidate[];
  selections: Selection[];
  plan: Plan | null;
  invocations: Record<string, Invocation>;
  approvals: Record<string, Approval>;
  tasks: Record<string, DurableTask>;
  evidenceIds: string[];
  answer: string | null;
  citationNumbers: number[];
  durationMs: number | null;
}

export interface JsonPatchOperation {
  op: "add" | "remove" | "replace";
  path: string;
  value?: unknown;
}

export interface ActivityContent {
  stage: string;
  label: string;
  status: "pending" | "running" | "complete" | "failed" | "cancelled";
  sourceEventType: string;
  agentId?: string | null;
  invocationId?: string | null;
  evidenceIds: string[];
}

export type ProjectedEvent =
  | {
      type: "STATE_SNAPSHOT";
      snapshot: CanvasState;
      timestamp: number;
    }
  | {
      type: "STATE_DELTA";
      delta: JsonPatchOperation[];
      timestamp: number;
    }
  | {
      type: "ACTIVITY_SNAPSHOT";
      messageId: string;
      activityType: "agent_iq.lifecycle";
      content: ActivityContent;
      replace: boolean;
      timestamp: number;
    }
  | {
      type:
        | "RUN_STARTED"
        | "RUN_FINISHED"
        | "RUN_ERROR"
        | "STEP_STARTED"
        | "STEP_FINISHED"
        | "TEXT_MESSAGE_START"
        | "TEXT_MESSAGE_CONTENT"
        | "TEXT_MESSAGE_END"
        | "TOOL_CALL_START"
        | "TOOL_CALL_END";
      timestamp: number;
      [key: string]: unknown;
    };

export interface ProjectionEnvelope {
  projectionId: string;
  sourceEventId: string;
  sourceSequence: number;
  projectionIndex: number;
  executionId: string;
  traceId: string;
  event: ProjectedEvent;
}

export interface Projection {
  schemaVersion: string;
  agUiReferenceRevision: string;
  executionId: string;
  threadId: string;
  canonicalEventCount: number;
  eventCount: number;
  events: ProjectionEnvelope[];
}

export interface CanvasHostState {
  projection: Projection;
  cursor: number;
  mode: "demo" | "live";
  source: string;
  status: string;
}
