import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const FORMATION_WIDGET = fileURLToPath(
  new URL("../../../../src/agent_iq/api/widgets/formation.html", import.meta.url),
);

const selectedAgent = {
  agentId: "employee-it-helpdesk",
  displayName: "IT Helpdesk Agent",
  reason: "Selected IT Helpdesk Agent for: My VPN is down",
  matchedSkills: ["vpn-recovery-guidance"],
  matchedTags: ["vpn", "remote access"],
};

const source = {
  number: 1,
  title: "VPN recovery guidance snapshot",
  sourceId: "employee-vpn-recovery-guidance",
  excerpt: "Aggregate guidance only; not a device diagnosis.",
};

const payload = {
  schemaVersion: "1.0",
  executionId: "exec-vpn",
  requestId: "request-vpn",
  status: "completed",
  durationMs: 824,
  selectedAgents: [selectedAgent],
  answer:
    "2,400 resolved VPN recoveries across Global from 14 Jun 2026 to 13 Jul 2026. Start with reconnect the VPN once.",
  citations: [source],
  sourcesMarkdown: "1. VPN recovery guidance snapshot",
  issues: [],
  vpnRecovery: {
    status: "available",
    title: "VPN recovery guidance",
    summaryLines: [
      "2,400 resolved VPN recoveries across Global from 14 Jun 2026 to 13 Jul 2026.",
      "Start with reconnect the VPN once. If two reconnect attempts or one restart do not restore access, contact the help desk.",
    ],
    population: 2400,
    periodLabel: "14 Jun 2026 - 13 Jul 2026",
    scopeLabel: "Global",
    recommendedFirstAction: "Reconnect the VPN once",
    escalationBoundary:
      "If two reconnect attempts or one restart do not restore access, contact the help desk.",
    freshnessLabel: "Freshness: 14 Jul 2026 09:00 UTC",
    guidanceNote: "Aggregate recovery guidance only; not a device diagnosis.",
    readOnlyLabel: "Read-only guidance only; no action was taken.",
    selectedAgent,
    source,
    outcomes: [
      { outcomeId: "reconnect", label: "Reconnect", count: 1260, share: 0.525, recommended: true },
      { outcomeId: "restart", label: "Restart device", count: 648, share: 0.27, recommended: false },
      { outcomeId: "network_reset", label: "Reset network stack", count: 252, share: 0.105, recommended: false },
      { outcomeId: "help_desk", label: "Help desk escalation", count: 240, share: 0.1, recommended: false },
    ],
  },
};

function bootstrapHtml(toolOutput: Record<string, unknown>): string {
  const host = `
    <script>
      window.__formationHost = { initialized: false };
      window.openai = { toolOutput: ${JSON.stringify(toolOutput)} };
      window.addEventListener("message", (event) => {
        const message = event.data;
        if (!message || typeof message !== "object") return;
        if (message.method === "ui/initialize") {
          window.postMessage({
            jsonrpc: "2.0",
            id: message.id,
            result: {
              protocolVersion: "2026-01-26",
              hostCapabilities: { openLinks: false },
              hostContext: {
                theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
                displayMode: "inline",
                availableDisplayModes: ["inline"],
                containerDimensions: { maxHeight: 900 }
              }
            }
          }, "*");
        } else if (message.method === "ui/notifications/initialized") {
          window.__formationHost.initialized = true;
        }
      });
    </script>`;
  return readFileSync(FORMATION_WIDGET, "utf8").replace("</head>", `${host}</head>`);
}

function bootstrapRestoredHtml(toolOutput: Record<string, unknown>): string {
  const host = `
    <script>
      window.setTimeout(() => {
        window.openai = { toolOutput: ${JSON.stringify(toolOutput)} };
      }, 100);
    </script>`;
  return readFileSync(FORMATION_WIDGET, "utf8").replace("</head>", `${host}</head>`);
}

async function render(page: Page, toolOutput: Record<string, unknown>): Promise<void> {
  await page.setContent(bootstrapHtml(toolOutput), { waitUntil: "load" });
  await expect(page.locator("#app")).toHaveAttribute("aria-busy", "false");
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (
            window as typeof window & {
              __formationHost: { initialized: boolean };
            }
          ).__formationHost.initialized,
      ),
    )
    .toBe(true);
}

async function expectNoWcagViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(
    results.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      targets: violation.nodes.map((node) => node.target),
    })),
    JSON.stringify(results.violations, null, 2),
  ).toEqual([]);
}

for (const colorScheme of ["light", "dark"] as const) {
  test(`VPN recovery is accessible in ${colorScheme} mode`, async ({ page }) => {
    await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
    await page.setViewportSize({ width: 390, height: 820 });
    await render(page, payload);

    await expect(page.locator(".answer-card h2")).toHaveText("VPN recovery guidance");
    await expect(page.getByText("Read-only guidance only; no action was taken.")).toBeVisible();
    await expect(page.locator(".answer-card")).toHaveCount(1);
    await expect(page.locator(".agent-event")).toHaveCount(0);
    await expect(page.locator("button")).toHaveCount(0);
    await expectNoWcagViolations(page);
  });
}

for (const width of [280, 320, 390]) {
  test(`VPN recovery uses the vertical fallback at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 820 });
    await render(page, payload);

    const geometry = await page.evaluate(() => ({
      horizontalOverflow:
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      mobileCards: document.querySelectorAll(".vpn-flow-mobile .vpn-mobile-card").length,
      desktopVisible: getComputedStyle(
        document.querySelector(".vpn-flow") as Element,
      ).display,
    }));

    expect(geometry.horizontalOverflow).toBe(false);
    expect(geometry.mobileCards).toBe(5);
    expect(geometry.desktopVisible).toBe("none");
  });
}

test("VPN recovery shows the desktop Sankey-inspired flow and subtle provenance", async ({
  page,
}) => {
  await page.setViewportSize({ width: 920, height: 820 });
  await render(page, payload);

  await expect(page.locator(".vpn-flow")).toBeVisible();
  await expect(page.locator(".vpn-flow-mobile")).toBeHidden();
  await expect(page.getByText("Selected agent: IT Helpdesk Agent")).toBeVisible();
  await expect(page.getByText("VPN recovery guidance snapshot")).toBeVisible();
  await expect(page.getByText("Aggregate recovery guidance only; not a device diagnosis.")).toBeVisible();
  await page.locator(".source-pill summary").click();
  await expect(page.getByText("Aggregate guidance only; not a device diagnosis.")).toBeVisible();
  await expect(page.locator(".answer-card")).toHaveCount(1);
});

test("VPN recovery fails closed when authenticated guidance is unavailable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 820 });
  await render(page, {
    ...payload,
    answer: "Authenticated VPN guidance was unavailable for this run.",
    issues: [{ agentId: "employee-it-helpdesk", status: "omitted_ungrounded", message: "Authenticated VPN guidance was unavailable for this run." }],
    vpnRecovery: {
      status: "unavailable",
      title: "VPN guidance unavailable",
      summaryLines: ["Authenticated VPN guidance was unavailable for this run."],
      readOnlyLabel: "Read-only guidance only; no action was taken.",
      outcomes: [],
    },
  });

  await expect(page.locator(".answer-card h2")).toHaveText("VPN guidance unavailable");
  await expect(page.locator(".vpn-flow")).toHaveCount(0);
  await expect(page.locator(".vpn-summary p")).toHaveText(
    "Authenticated VPN guidance was unavailable for this run.",
  );
});

test("VPN recovery restores a saved result when host globals arrive late", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 820 });
  await page.setContent(bootstrapRestoredHtml(payload), { waitUntil: "load" });

  await expect(page.locator(".answer-card h2")).toHaveText("VPN recovery guidance");
  await expect(page.locator("#app")).toHaveAttribute("aria-busy", "false");
  await expect(page.getByText("Read-only guidance only; no action was taken.")).toBeVisible();
});
