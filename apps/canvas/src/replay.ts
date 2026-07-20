import type {
  ActivityContent,
  CanvasState,
  JsonPatchOperation,
  Projection,
  ProjectionEnvelope,
} from "./types";

const STATE_FIELDS = new Set<keyof CanvasState>([
  "executionId",
  "status",
  "stage",
  "catalogVersion",
  "candidateCount",
  "candidates",
  "selections",
  "plan",
  "invocations",
  "approvals",
  "tasks",
  "evidenceIds",
  "answer",
  "citationNumbers",
  "durationMs",
]);

const RESTRICTED_KEYS = new Set([
  "requestid",
  "intent",
  "query",
  "subjectid",
  "tenantid",
  "contextid",
  "sourceuri",
  "toolcallargs",
  "arguments",
  "providererror",
  "reasoning",
  "chaineofthought",
  "chainofthought",
  "rawevent",
  "prompt",
  "credential",
  "accesstoken",
]);

export class ProjectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProjectionError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function assertNoRestrictedFields(value: unknown, path = "$"): void {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => assertNoRestrictedFields(entry, `${path}[${index}]`));
    return;
  }
  if (!isRecord(value)) {
    return;
  }
  for (const [key, entry] of Object.entries(value)) {
    const normalized = key.replaceAll("_", "").toLowerCase();
    if (RESTRICTED_KEYS.has(normalized)) {
      throw new ProjectionError(`Restricted field ${path}.${key} is not allowed in Canvas data.`);
    }
    assertNoRestrictedFields(entry, `${path}.${key}`);
  }
}

export function validateProjection(value: unknown): Projection {
  assertNoRestrictedFields(value);
  if (!isRecord(value) || !Array.isArray(value.events)) {
    throw new ProjectionError("Canvas data is not an AG-UI projection.");
  }
  if (
    typeof value.executionId !== "string" ||
    typeof value.threadId !== "string" ||
    typeof value.eventCount !== "number" ||
    typeof value.canonicalEventCount !== "number"
  ) {
    throw new ProjectionError("Canvas projection identifiers or counts are invalid.");
  }
  if (value.eventCount !== value.events.length || value.events.length === 0) {
    throw new ProjectionError("Canvas projection event count does not match its event list.");
  }
  const events = value.events as ProjectionEnvelope[];
  if (events[0]?.event?.type !== "RUN_STARTED") {
    throw new ProjectionError("Canvas projection must begin with RUN_STARTED.");
  }
  for (const envelope of events) {
    if (envelope.executionId !== value.executionId) {
      throw new ProjectionError("Canvas projection mixes execution identifiers.");
    }
  }
  return value as unknown as Projection;
}

function decodePointer(path: string): keyof CanvasState {
  if (!/^\/[^/]+$/.test(path)) {
    throw new ProjectionError("Canvas accepts only top-level RFC 6902 state patches.");
  }
  const decoded = path.slice(1).replaceAll("~1", "/").replaceAll("~0", "~");
  if (!STATE_FIELDS.has(decoded as keyof CanvasState)) {
    throw new ProjectionError(`Unknown Canvas state field: ${decoded}.`);
  }
  return decoded as keyof CanvasState;
}

function applyOperation(state: CanvasState, operation: JsonPatchOperation): CanvasState {
  const field = decodePointer(operation.path);
  const next = cloneJson(state) as unknown as Record<string, unknown>;
  if (operation.op === "remove") {
    if (!(field in next)) {
      throw new ProjectionError(`Cannot remove missing Canvas state field: ${field}.`);
    }
    delete next[field];
    return next as unknown as CanvasState;
  }
  if (operation.value === undefined) {
    throw new ProjectionError(`${operation.op} requires a Canvas state value.`);
  }
  if (operation.op === "replace" && !(field in next)) {
    throw new ProjectionError(`Cannot replace missing Canvas state field: ${field}.`);
  }
  next[field] = cloneJson(operation.value);
  return next as unknown as CanvasState;
}

function emptyState(executionId: string): CanvasState {
  return {
    executionId,
    status: "running",
    stage: "accepted",
    catalogVersion: null,
    candidateCount: 0,
    candidates: [],
    selections: [],
    plan: null,
    invocations: {},
    approvals: {},
    tasks: {},
    evidenceIds: [],
    answer: null,
    citationNumbers: [],
    durationMs: null,
  };
}

export function replayAt(projection: Projection, requestedCursor: number): CanvasState {
  const cursor = Math.max(-1, Math.min(requestedCursor, projection.events.length - 1));
  let state: CanvasState | null = null;
  for (let index = 0; index <= cursor; index += 1) {
    const event = projection.events[index]?.event;
    if (event?.type === "STATE_SNAPSHOT") {
      state = cloneJson(event.snapshot);
    } else if (event?.type === "STATE_DELTA") {
      if (state === null) {
        throw new ProjectionError("STATE_DELTA appeared before STATE_SNAPSHOT.");
      }
      state = event.delta.reduce(applyOperation, state);
    }
  }
  return state ?? emptyState(projection.executionId);
}

export interface ActivityEntry extends ActivityContent {
  id: string;
  timestamp: number;
  sourceSequence: number;
}

export function activitiesAt(projection: Projection, requestedCursor: number): ActivityEntry[] {
  const cursor = Math.max(-1, Math.min(requestedCursor, projection.events.length - 1));
  const activities: ActivityEntry[] = [];
  for (let index = 0; index <= cursor; index += 1) {
    const envelope = projection.events[index];
    if (envelope?.event.type !== "ACTIVITY_SNAPSHOT") {
      continue;
    }
    activities.push({
      ...envelope.event.content,
      id: envelope.projectionId,
      timestamp: envelope.event.timestamp,
      sourceSequence: envelope.sourceSequence,
    });
  }
  return activities;
}

export function cursorForSourceSequence(projection: Projection, sourceSequence: number): number {
  let cursor = 0;
  projection.events.forEach((envelope, index) => {
    if (envelope.sourceSequence <= sourceSequence) {
      cursor = index;
    }
  });
  return cursor;
}

export function sourceSequenceAt(projection: Projection, cursor: number): number {
  return projection.events[Math.max(0, Math.min(cursor, projection.events.length - 1))]
    ?.sourceSequence ?? 0;
}
