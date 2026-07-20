import { fireEvent, render, screen } from "@testing-library/react";

import demoProjection from "../public/demo/launch-command-center.agui.json";
import { CommandCenter } from "./App";
import { validateProjection } from "./replay";

describe("Agent IQ command center", () => {
  test("renders the formation, DAG, provenance, approval, and durable handoff", () => {
    const projection = validateProjection(demoProjection);
    const onCursorChange = vi.fn();
    render(
      <CommandCenter
        hostState={{
          projection,
          cursor: projection.events.length - 1,
          mode: "demo",
          source: "Test replay",
          status: "ready",
        }}
        cursor={projection.events.length - 1}
        onCursorChange={onCursorChange}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "EU Cold-Water Launch Command Center" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "5 specialists, one governed team" }),
    ).toBeInTheDocument();
    expect(screen.getByText("6 steps · up to 4 in parallel")).toBeInTheDocument();
    expect(screen.getByText("5 governed evidence objects")).toBeInTheDocument();
    expect(screen.getByText("Approved approval")).toBeInTheDocument();
    expect(screen.getByText("Completed durable task")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Conditional go" })).toBeInTheDocument();
    expect(screen.queryByText(/chain of thought/i)).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("slider", { name: "Replay position" }), {
      target: { value: "2" },
    });
    expect(onCursorChange).toHaveBeenCalledWith(2);
  });

  test("exposes semantic landmarks and a labelled lifecycle", () => {
    const projection = validateProjection(demoProjection);
    render(
      <CommandCenter
        hostState={{
          projection,
          cursor: projection.events.length - 1,
          mode: "demo",
          source: "Test replay",
          status: "ready",
        }}
        cursor={projection.events.length - 1}
        onCursorChange={() => undefined}
      />,
    );

    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Agent IQ lifecycle" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Execution plan phases" })).toBeInTheDocument();
    expect(screen.getByLabelText("Execution replay controls")).toBeInTheDocument();
  });
});
