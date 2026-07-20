import { fileURLToPath } from "node:url";

import {
  CanvasError,
  createCanvas,
  joinSession,
} from "@github/copilot-sdk/extension";

import { assertIdentifier, CanvasRuntime } from "./runtime.mjs";

const runtime = new CanvasRuntime({
  webRoot: fileURLToPath(new URL("./web", import.meta.url)),
  demoPath: fileURLToPath(
    new URL("./web/demo/launch-command-center.agui.json", import.meta.url),
  ),
});
await runtime.start();

function objectInput(input) {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    throw new CanvasError("invalid_input", "Canvas action input must be an object.");
  }
  return input;
}

const canvas = createCanvas({
  id: "agent-iq-command-center",
  displayName: "Agent IQ Command Center",
  description:
    "Replay a governed Agent IQ formation, execution DAG, evidence trail, approval, and durable handoff.",
  inputSchema: {
    type: "object",
    properties: {
      executionId: {
        type: "string",
        description: "Authorized Agent IQ execution identifier to follow.",
      },
      threadId: {
        type: "string",
        description: "Bounded replay thread identifier.",
      },
    },
    additionalProperties: false,
  },
  actions: [
    {
      name: "load_demo",
      description: "Load the deterministic synthetic five-agent launch command-center replay.",
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
      handler({ instanceId }) {
        const state = runtime.loadDemo(assertIdentifier(instanceId, "Canvas instance ID"));
        return {
          ok: true,
          executionId: state.projection.executionId,
          mode: state.mode,
          status: state.status,
        };
      },
    },
    {
      name: "load_execution",
      description:
        "Load an authorized execution from the safe Agent IQ AG-UI projection endpoint.",
      inputSchema: {
        type: "object",
        properties: {
          executionId: { type: "string" },
          threadId: { type: "string" },
        },
        required: ["executionId"],
        additionalProperties: false,
      },
      async handler({ instanceId, input }) {
        const values = objectInput(input);
        const executionId = assertIdentifier(values.executionId, "Execution ID");
        const threadId =
          typeof values.threadId === "string"
            ? assertIdentifier(values.threadId, "Thread ID")
            : `canvas-${assertIdentifier(instanceId, "Canvas instance ID")}`;
        const state = await runtime.loadExecution(instanceId, executionId, threadId);
        return {
          ok: true,
          executionId: state.projection.executionId,
          mode: state.mode,
          status: state.status,
        };
      },
    },
    {
      name: "set_replay_cursor",
      description: "Move the open command center to a projected event index.",
      inputSchema: {
        type: "object",
        properties: {
          cursor: { type: "integer", minimum: 0 },
        },
        required: ["cursor"],
        additionalProperties: false,
      },
      handler({ instanceId, input }) {
        const values = objectInput(input);
        const state = runtime.setCursor(instanceId, values.cursor);
        return {
          ok: true,
          cursor: state.cursor,
          eventCount: state.projection.eventCount,
        };
      },
    },
  ],
  async open({ instanceId, input }) {
    try {
      const state = await runtime.openInstance(instanceId, input);
      return {
        url: runtime.url(instanceId),
        title: "Agent IQ Command Center",
        status: state.mode === "live" ? "following authorized execution" : "demo replay ready",
      };
    } catch (error) {
      throw new CanvasError(
        "open_failed",
        error instanceof Error ? error.message : "Agent IQ Canvas could not open.",
      );
    }
  },
  onClose({ instanceId }) {
    runtime.closeInstance(instanceId);
  },
});

await joinSession({ canvases: [canvas] });

let closing = false;
async function close() {
  if (closing) return;
  closing = true;
  await runtime.stop();
}

process.once("SIGINT", () => void close().finally(() => process.exit(0)));
process.once("SIGTERM", () => void close().finally(() => process.exit(0)));
process.once("beforeExit", () => void close());
