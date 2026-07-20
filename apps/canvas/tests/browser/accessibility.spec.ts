import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const FORMATION_WIDGET = fileURLToPath(
  new URL("../../../../src/agent_iq/api/widgets/formation.html", import.meta.url),
);

type FormationHostState = {
  initialized: boolean;
  messages: Array<{ id?: number; method?: string }>;
  protocolViolation: boolean;
  sizeNotifications: number;
};

async function openCanvas(page: Page): Promise<void> {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "EU Cold-Water Launch Command Center" }),
  ).toBeVisible();
}

async function expectNoWcagViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  const summary = results.violations.map((violation) => ({
    id: violation.id,
    impact: violation.impact,
    targets: violation.nodes.map((node) => node.target),
  }));
  expect(summary, JSON.stringify(summary, null, 2)).toEqual([]);
}

async function layoutSignature(page: Page) {
  return page.evaluate(() => {
    const columns = (selector: string): number => {
      const positions = [...document.querySelectorAll<HTMLElement>(selector)]
        .filter((element) => element.getBoundingClientRect().width > 0)
        .map((element) => element.getBoundingClientRect().x)
        .sort((left, right) => left - right);
      const distinct: number[] = [];
      for (const position of positions) {
        if (distinct.every((seen) => Math.abs(seen - position) > 2)) distinct.push(position);
      }
      return distinct.length;
    };
    const root = document.documentElement;
    const topbar = document.querySelector<HTMLElement>(".topbar");
    const replayButton = document.querySelector<HTMLElement>(".replay-button");
    const buttonRect = replayButton?.getBoundingClientRect();
    return {
      horizontalOverflow: root.scrollWidth > root.clientWidth + 1,
      stageColumns: columns(".stage-rail > li"),
      squadColumns: columns(".squad-grid > .agent-card"),
      planColumns: columns(".two-column:first-of-type > *"),
      answerColumns: columns(".answer-panel > *"),
      topbarPosition: topbar === null ? null : getComputedStyle(topbar).position,
      replayTarget:
        buttonRect === undefined
          ? null
          : {
              width: Math.round(buttonRect.width),
              height: Math.round(buttonRect.height),
            },
    };
  });
}

for (const colorScheme of ["dark", "light"] as const) {
  test(`Canvas has no automated WCAG A/AA violations in ${colorScheme} mode`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme });
    await openCanvas(page);

    await expectNoWcagViolations(page);
    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.getByRole("region", { name: "Execution replay controls" })).toBeVisible();
    await expect(page.getByRole("list", { name: "Recorded lifecycle activity" })).toBeVisible();
  });
}

const layouts = [
  {
    name: "desktop",
    viewport: { width: 1440, height: 1000 },
    expected: {
      stageColumns: 6,
      squadColumns: 5,
      planColumns: 2,
      answerColumns: 2,
      topbarPosition: "sticky",
    },
  },
  {
    name: "compact desktop",
    viewport: { width: 900, height: 900 },
    expected: {
      stageColumns: 6,
      squadColumns: 3,
      planColumns: 1,
      answerColumns: 2,
      topbarPosition: "sticky",
    },
  },
  {
    name: "Copilot right panel",
    viewport: { width: 520, height: 900 },
    expected: {
      stageColumns: 3,
      squadColumns: 1,
      planColumns: 1,
      answerColumns: 1,
      topbarPosition: "relative",
    },
  },
  {
    name: "320px reflow",
    viewport: { width: 320, height: 800 },
    expected: {
      stageColumns: 3,
      squadColumns: 1,
      planColumns: 1,
      answerColumns: 1,
      topbarPosition: "relative",
    },
  },
] as const;

for (const profile of layouts) {
  test(`preserves the governed layout contract at ${profile.name}`, async ({ page }) => {
    await page.setViewportSize(profile.viewport);
    await openCanvas(page);

    const signature = await layoutSignature(page);
    expect(signature.horizontalOverflow).toBe(false);
    expect(signature).toMatchObject(profile.expected);
    expect(signature.replayTarget?.width).toBeGreaterThanOrEqual(24);
    expect(signature.replayTarget?.height).toBeGreaterThanOrEqual(24);
  });
}

test("reduced motion converts autoplay into keyboard-controlled steps", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openCanvas(page);

  await page.keyboard.press("Tab");
  const replayButton = page.getByRole("button", { name: "Restart replay" });
  await expect(replayButton).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("slider", { name: "Replay position" })).toHaveValue("0");
  await expect(page.getByRole("button", { name: "Next event" })).toBeVisible();
  await page.waitForTimeout(600);
  await expect(page.getByRole("slider", { name: "Replay position" })).toHaveValue("0");

  await page.keyboard.press("Tab");
  const slider = page.getByRole("slider", { name: "Replay position" });
  await expect(slider).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(slider).toHaveValue("1");

  const reducedDuration = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "pulse";
    document.body.append(probe);
    const duration = getComputedStyle(probe).animationDuration;
    probe.remove();
    return duration;
  });
  expect(Number.parseFloat(reducedDuration)).toBeLessThanOrEqual(0.001);
});

test("compact MCP fallback is WCAG-clean and reflows at its 280px boundary", async ({ page }) => {
  await page.setViewportSize({ width: 280, height: 760 });
  await page.setContent(readFileSync(FORMATION_WIDGET, "utf8"), { waitUntil: "load" });

  await expect(page.getByRole("heading", { name: "Forming the right specialist team" })).toBeVisible();
  await expectNoWcagViolations(page);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect(overflow).toBe(false);
});

test("MCP widget initializes with stable one-shot sizing", async ({ page }) => {
  const widgetHtml = readFileSync(FORMATION_WIDGET, "utf8");
  await page.setContent('<iframe id="formation-widget" sandbox="allow-scripts"></iframe>');
  await page.evaluate((html) => {
    const iframe = document.querySelector<HTMLIFrameElement>("#formation-widget");
    if (!iframe) throw new Error("Formation iframe was not created");

    const state: FormationHostState = {
      initialized: false,
      messages: [],
      protocolViolation: false,
      sizeNotifications: 0,
    };
    const hostWindow = window as typeof window & { __formationHost?: FormationHostState };
    hostWindow.__formationHost = state;

    window.addEventListener("message", (event) => {
      if (event.source !== iframe.contentWindow || !event.data || typeof event.data !== "object") {
        return;
      }
      const message = event.data as { id?: number; method?: string };
      state.messages.push({ id: message.id, method: message.method });

      if (message.method === "ui/initialize" && message.id !== undefined) {
        window.setTimeout(() => {
          if (state.protocolViolation) return;
          iframe.contentWindow?.postMessage(
            {
              jsonrpc: "2.0",
              id: String(message.id),
              result: {
                hostCapabilities: {},
                hostContext: { theme: "light" },
                hostInfo: { name: "strict-test-host", version: "1.0.0" },
                protocolVersion: "2026-01-26",
              },
            },
            "*",
          );
        }, 75);
        return;
      }

      if (!state.initialized && message.method !== "ui/notifications/initialized") {
        state.protocolViolation = true;
        return;
      }
      if (message.method === "ui/notifications/initialized") state.initialized = true;
      if (message.method === "ui/notifications/size-changed") {
        state.sizeNotifications += 1;
        iframe.style.height = "195px";
      }
    });

    iframe.srcdoc = html;
  }, widgetHtml);

  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            (window as typeof window & { __formationHost?: FormationHostState }).__formationHost
              ?.initialized,
        ),
      { timeout: 1_500 },
    )
    .toBe(true);
  await expect
    .poll(() =>
      page.evaluate(() =>
        (
          window as typeof window & { __formationHost?: FormationHostState }
        ).__formationHost?.messages.some(
          (message) => message.method === "ui/notifications/size-changed",
        ),
      ),
    )
    .toBe(true);

  const state = await page.evaluate(
    () =>
      (window as typeof window & { __formationHost?: FormationHostState }).__formationHost ??
      null,
  );
  expect(state).not.toBeNull();
  expect(state?.protocolViolation).toBe(false);
  expect(state?.sizeNotifications).toBe(1);
  expect(state?.messages.slice(0, 3).map((message) => message.method)).toEqual([
    "ui/initialize",
    "ui/notifications/initialized",
    "ui/notifications/size-changed",
  ]);
  await page.waitForTimeout(250);
  const stableSizeCount = await page.evaluate(
    () =>
      (window as typeof window & { __formationHost?: FormationHostState }).__formationHost
        ?.sizeNotifications,
  );
  expect(stableSizeCount).toBe(1);
});
