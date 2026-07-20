import type { CanvasHostState } from "./types";
import { validateProjection } from "./replay";

const DEMO_REPLAY = "./demo/launch-command-center.agui.json";

interface ExtensionContext {
  instanceId: string;
  token: string;
}

function extensionContext(): ExtensionContext | null {
  const parameters = new URLSearchParams(window.location.search);
  const instanceId = parameters.get("instanceId");
  const token = parameters.get("token");
  return instanceId && token ? { instanceId, token } : null;
}

function stateUrl(context: ExtensionContext): string {
  const parameters = new URLSearchParams({
    instanceId: context.instanceId,
    token: context.token,
  });
  return `./api/state?${parameters.toString()}`;
}

function eventsUrl(context: ExtensionContext): string {
  const parameters = new URLSearchParams({
    instanceId: context.instanceId,
    token: context.token,
  });
  return `./api/events?${parameters.toString()}`;
}

export async function loadHostState(): Promise<CanvasHostState> {
  const context = extensionContext();
  if (context === null) {
    const response = await fetch(DEMO_REPLAY, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error("The bundled synthetic Canvas certification replay could not be loaded.");
    }
    const projection = validateProjection(await response.json());
    return {
      projection,
      cursor: projection.events.length - 1,
      mode: "demo",
      source: "Bundled synthetic certification replay",
      status: "ready",
    };
  }

  const response = await fetch(stateUrl(context), {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error("The Copilot Canvas extension did not return an authorized replay.");
  }
  const payload = (await response.json()) as CanvasHostState;
  return {
    ...payload,
    projection: validateProjection(payload.projection),
  };
}

export function subscribeHostState(
  onState: (state: CanvasHostState) => void,
  onError: () => void,
): () => void {
  const context = extensionContext();
  if (context === null) {
    return () => undefined;
  }
  const stream = new EventSource(eventsUrl(context));
  stream.addEventListener("state", (event) => {
    try {
      const payload = JSON.parse(event.data) as CanvasHostState;
      onState({
        ...payload,
        projection: validateProjection(payload.projection),
      });
    } catch {
      onError();
    }
  });
  stream.addEventListener("error", onError);
  return () => stream.close();
}

export async function updateHostCursor(cursor: number): Promise<void> {
  const context = extensionContext();
  if (context === null) {
    return;
  }
  const response = await fetch("./api/cursor", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Agent-IQ-Token": context.token,
    },
    body: JSON.stringify({ instanceId: context.instanceId, cursor }),
  });
  if (!response.ok) {
    throw new Error("The replay cursor could not be synchronized with the Canvas extension.");
  }
}
