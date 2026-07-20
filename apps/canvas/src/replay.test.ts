import demoProjection from "../public/demo/launch-command-center.agui.json";

import {
  ProjectionError,
  activitiesAt,
  cursorForSourceSequence,
  replayAt,
  validateProjection,
} from "./replay";

describe("safe AG-UI replay", () => {
  test("reconstructs the certified five-agent terminal state", () => {
    const projection = validateProjection(demoProjection);
    const state = replayAt(projection, projection.events.length - 1);

    expect(state.status).toBe("completed");
    expect(state.selections).toHaveLength(5);
    expect(state.plan?.steps).toHaveLength(6);
    expect(state.plan?.maxConcurrency).toBe(4);
    expect(state.evidenceIds).toHaveLength(5);
    expect(state.citationNumbers).toEqual([1, 2, 3, 4, 5]);
    expect(state.approvals["approval-monitoring-handoff"]?.status).toBe("approved");
    expect(state.tasks["task-launch-monitoring"]?.status).toBe("completed");
  });

  test("replays a canonical prefix without using future state", () => {
    const projection = validateProjection(demoProjection);
    const cursor = cursorForSourceSequence(projection, 3);
    const state = replayAt(projection, cursor);

    expect(state.selections).toHaveLength(5);
    expect(state.plan).toBeNull();
    expect(Object.keys(state.invocations)).toHaveLength(0);
    expect(state.answer).toBeNull();
    expect(activitiesAt(projection, cursor).at(-1)?.stage).toBe("agents_selected");
  });

  test("rejects restricted fields and nested patches", () => {
    const withIntent = structuredClone(demoProjection) as unknown as Record<string, unknown>;
    withIntent.intent = "must not render";
    expect(() => validateProjection(withIntent)).toThrow(ProjectionError);

    const projection = validateProjection(structuredClone(demoProjection));
    const stateDelta = projection.events.find((item) => item.event.type === "STATE_DELTA");
    if (stateDelta?.event.type !== "STATE_DELTA") {
      throw new Error("Expected a state delta in the demo projection.");
    }
    stateDelta.event.delta[0] = {
      op: "replace",
      path: "/invocations/private",
      value: {},
    };
    expect(() => replayAt(projection, projection.events.length - 1)).toThrow(
      "top-level RFC 6902",
    );
  });
});
