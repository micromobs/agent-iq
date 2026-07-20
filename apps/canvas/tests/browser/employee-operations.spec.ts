import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const EMPLOYEE_WIDGET = fileURLToPath(
  new URL("../../../../src/agent_iq/api/widgets/employee-operations.html", import.meta.url),
);

const item = (
  itemId: string,
  label: string,
  extra: Record<string, unknown> = {},
): Record<string, unknown> => ({
  itemId,
  label,
  value: null,
  description: null,
  state: null,
  href: null,
  metricValue: null,
  values: [],
  tags: [],
  ...extra,
});

const experience = {
  schemaVersion: "1.0",
  scenarioId: "browser-governance-showcase",
  category: "site_governance",
  dataScope: "Results are scoped to the selected employee-services workflow.",
  readOnlyDisclosure:
    "Draft and review experience only. No enterprise system is changed by this preview.",
  title: "Employee Operations",
  subtitle: "All rich section kinds, observable stages, evidence, and safe actions.",
  status: "blocked",
  statusLabel: "Blocked by policy",
  selectedAgents: [
    {
      agentId: "employee-site-health",
      displayName: "Site Health Assistant",
      glyph: "HLTH",
      group: "management_governance",
      reason: "Explains the site finding and remediation status.",
    },
    {
      agentId: "employee-access-governance",
      displayName: "Access Governance Assistant",
      glyph: "ACC",
      group: "management_governance",
      reason: "Explains the affected entitlement and approver.",
    },
  ],
  phases: [
    {
      phaseId: "collect",
      agentId: "employee-site-health",
      title: "Collect approved evidence",
      status: "completed",
      statusLabel: "Completed",
      outputLabel: "Three approved sources selected",
      dependsOn: [],
    },
    {
      phaseId: "explain",
      agentId: "employee-access-governance",
      title: "Explain policy and remediation",
      status: "blocked",
      statusLabel: "Blocked",
      outputLabel: "Owner approval is required",
      dependsOn: ["collect"],
    },
  ],
  metrics: [
    { metricId: "findings", label: "Open findings", value: "3", trend: "down", helperText: "1 fewer" },
    { metricId: "risk", label: "Highest risk", value: "High", trend: null, helperText: "External sharing" },
  ],
  sections: [
    {
      sectionId: "dashboard",
      kind: "dashboard",
      title: "Dashboard",
      subtitle: "Key signals",
      columns: [],
      items: [item("risk", "Risk score", { value: "82", description: "Validated score" })],
      footnote: null,
    },
    {
      sectionId: "timeline",
      kind: "timeline",
      title: "Timeline",
      subtitle: null,
      columns: [],
      items: [item("review", "Policy reviewed", { state: "completed", description: "Reviewed today" })],
      footnote: null,
    },
    {
      sectionId: "form",
      kind: "form",
      title: "Review fields",
      subtitle: null,
      columns: [],
      items: [item("business-reason", "Business reason", { value: "Project collaboration" })],
      footnote: "Review before preparing an action.",
    },
    {
      sectionId: "comparison",
      kind: "comparison_table",
      title: "Options",
      subtitle: null,
      columns: ["Current", "Recommended"],
      items: [item("sharing", "External sharing", { values: ["Anyone link", "Named guests"] })],
      footnote: null,
    },
    {
      sectionId: "checklist",
      kind: "checklist",
      title: "Checklist",
      subtitle: null,
      columns: [],
      items: [item("owner", "Confirm resource owner", { state: "pending" })],
      footnote: null,
    },
    {
      sectionId: "chart",
      kind: "chart",
      title: "Finding trend",
      subtitle: null,
      columns: [],
      items: [item("week", "This week", { value: "3", metricValue: 3 })],
      footnote: null,
    },
    {
      sectionId: "people",
      kind: "people_cards",
      title: "People",
      subtitle: null,
      columns: [],
      items: [item("owner-person", "Avery Morgan", { value: "Site owner", description: "Site owner" })],
      footnote: null,
    },
    {
      sectionId: "org",
      kind: "org_chart",
      title: "Approval chain",
      subtitle: null,
      columns: [],
      items: [
        item("requester", "Jordan Lee", { value: "Requester" }),
        item("approver", "Avery Morgan", { value: "Site owner" }),
      ],
      footnote: null,
    },
    {
      sectionId: "map",
      kind: "map",
      title: "Campus map",
      subtitle: null,
      columns: [],
      items: [item("building", "Building 1", { description: "Governance office" })],
      footnote: null,
    },
    {
      sectionId: "gallery",
      kind: "gallery",
      title: "Templates",
      subtitle: null,
      columns: [],
      items: [item("template", "Controlled collaboration", { value: "Recommended", description: "Guest controls enabled", href: "https://example.test/template" })],
      footnote: null,
    },
    {
      sectionId: "documents",
      kind: "document_callouts",
      title: "Evidence",
      subtitle: null,
      columns: [],
      items: [item("policy-doc", "External sharing policy", { description: "Approved policy", href: "https://example.test/policy" })],
      footnote: null,
    },
    {
      sectionId: "decision",
      kind: "decision_tree",
      title: "Decision tree",
      subtitle: null,
      columns: [],
      items: [item("decision-node", "Are named guests configured?", { state: "no", description: "Remediation is required." })],
      footnote: null,
    },
    {
      sectionId: "approvals",
      kind: "approvals",
      title: "Approvals",
      subtitle: null,
      columns: [],
      items: [item("approval", "Site owner review", { state: "pending_approval", description: "Avery Morgan must review." })],
      footnote: null,
    },
    {
      sectionId: "findings",
      kind: "policy_findings",
      title: "Policy findings",
      subtitle: null,
      columns: [],
      items: [item("finding", "External sharing is too broad", { state: "critical", value: "Policy EXT-104", description: "Named guests are required." })],
      footnote: null,
    },
    {
      sectionId: "sources",
      kind: "source_drawer",
      title: "Source drawer",
      subtitle: null,
      columns: [],
      items: [item("source", "External sharing policy", { description: "Approved source", href: "https://example.test/source" })],
      footnote: null,
    },
    {
      sectionId: "follow-ups",
      kind: "follow_ups",
      title: "Follow-ups",
      subtitle: null,
      columns: [],
      items: [item("follow-up", "Show the compliant guest-access option")],
      footnote: null,
    },
  ],
  sources: [
    {
      sourceId: "source-1",
      title: "External sharing policy",
      knowledgeSourceId: "kb-policy-governance",
      summary: "Policy evidence.",
      url: "https://example.test/source",
    },
    {
      sourceId: "source-2",
      title: "Access review",
      knowledgeSourceId: "kb-access-governance",
      summary: "Entitlement evidence.",
      url: "https://example.test/access",
    },
    {
      sourceId: "source-3",
      title: "Site health report",
      knowledgeSourceId: "kb-site-health-dashboards",
      summary: "Health evidence.",
      url: "https://example.test/health",
    },
  ],
  actions: [
    {
      actionId: "view-source",
      kind: "view_source",
      actionClass: "local_ui",
      label: "View source",
      description: "Scroll to source evidence.",
      requiresApproval: false,
    },
    {
      actionId: "prepare-review",
      kind: "request_approval",
      actionClass: "send_message",
      label: "Prepare governance review",
      description: "Prepare a review request for the named approver.",
      requiresApproval: true,
    },
  ],
  trustBoundary: "No access, policy, site, or records mutation is executed.",
  suggestedPrompts: ["Show the compliant guest-access option"],
};

const catalog = {
  schemaVersion: "1.0",
  catalogId: "employee-scenario-catalog",
  dataScope: "Results are scoped to the selected employee-services workflow.",
  readOnlyDisclosure: "No enterprise action is executed.",
  groupSummaries: [
    { group: "line_of_business", agentCount: 10, agentIds: ["employee-hr-concierge"] },
    { group: "corporate_services", agentCount: 11, agentIds: ["employee-news"] },
    { group: "management_governance", agentCount: 5, agentIds: ["employee-site-health"] },
  ],
  scenarios: [
    {
      scenarioId: experience.scenarioId,
      category: experience.category,
      title: experience.title,
      subtitle: experience.subtitle,
      agentIds: experience.selectedAgents.map((agent) => agent.agentId),
      tags: ["governance", "blocked access", "all sections"],
    },
  ],
  totalAgents: 26,
  totalKnowledgeSources: 30,
};

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

function bootstrapHtml(widgetHtml: string): string {
  const bootstrap = `
    <script>
      window.openai = { toolOutput: ${JSON.stringify(experience)} };
      window.addEventListener("message", (event) => {
        const message = event.data;
        if (message && message.method === "ui/initialize") {
          window.postMessage({
            jsonrpc: "2.0",
            id: message.id,
            result: {
              protocolVersion: "2026-01-26",
              hostCapabilities: {},
              hostContext: {
                theme: "light",
                displayMode: "inline",
                availableDisplayModes: ["inline", "fullscreen"],
                containerDimensions: { maxHeight: 4000 }
              }
            }
          }, "*");
        }
      });
    </script>`;
  return widgetHtml.replace("</head>", `${bootstrap}</head>`);
}

test("employee widget renders every rich section without WCAG or 320px reflow failures", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setContent(bootstrapHtml(readFileSync(EMPLOYEE_WIDGET, "utf8")), {
    waitUntil: "load",
  });

  await expect(page.getByRole("heading", { name: experience.title })).toBeVisible();
  await expect(page.locator("[data-section-kind]")).toHaveCount(16);
  await expect(page.getByRole("region", { name: "Scenario actions" })).toBeVisible();
  await expectNoWcagViolations(page);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect(overflow).toBe(false);
});

test("employee widget obeys strict initialization, gallery tool calls, and safe receipt flow", async ({
  page,
}) => {
  const widgetHtml = readFileSync(EMPLOYEE_WIDGET, "utf8");
  await page.setContent(
    '<iframe id="employee-widget" title="Employee widget" sandbox="allow-scripts"></iframe>',
  );
  await page.evaluate(
    ({ html, catalogPayload, experiencePayload }) => {
      const iframe = document.querySelector<HTMLIFrameElement>("#employee-widget");
      if (!iframe) throw new Error("Employee iframe was not created");
      iframe.style.width = "320px";
      iframe.style.height = "900px";
      const state = {
        initialized: false,
        messages: [] as string[],
        protocolViolation: false,
        toolCalls: [] as string[],
      };
      (
        window as typeof window & {
          __employeeHost?: typeof state;
        }
      ).__employeeHost = state;

      const sendResult = (id: number | string, result: unknown): void => {
        iframe.contentWindow?.postMessage({ jsonrpc: "2.0", id: String(id), result }, "*");
      };

      window.addEventListener("message", (event) => {
        if (event.source !== iframe.contentWindow || !event.data || typeof event.data !== "object") {
          return;
        }
        const message = event.data as {
          id?: number | string;
          method?: string;
          params?: { name?: string; arguments?: Record<string, unknown> };
        };
        if (message.method) state.messages.push(message.method);
        if (message.method === "ui/initialize" && message.id !== undefined) {
          window.setTimeout(() => {
            sendResult(message.id as number | string, {
              protocolVersion: "2026-01-26",
              hostCapabilities: { serverTools: true, openLinks: true, message: true },
              hostContext: {
                theme: "light",
                displayMode: "inline",
                availableDisplayModes: ["inline", "fullscreen"],
                containerDimensions: { maxHeight: 900 },
              },
            });
          }, 50);
          return;
        }
        if (!state.initialized && message.method !== "ui/notifications/initialized") {
          state.protocolViolation = true;
          return;
        }
        if (message.method === "ui/notifications/initialized") {
          state.initialized = true;
          iframe.contentWindow?.postMessage(
            {
              jsonrpc: "2.0",
              method: "ui/notifications/tool-result",
              params: { structuredContent: catalogPayload },
            },
            "*",
          );
          return;
        }
        if (message.method === "tools/call" && message.id !== undefined) {
          const toolName = message.params?.name ?? "";
          state.toolCalls.push(toolName);
          if (toolName === "open_employee_experience") {
            sendResult(message.id, experiencePayload);
          } else if (toolName === "prepare_employee_action") {
            const callArguments = message.params?.arguments as
              | { request?: { confirmed?: boolean; form_values?: Record<string, string> } }
              | undefined;
            const confirmed = Boolean(callArguments?.request?.confirmed);
            const formValues = callArguments?.request?.form_values ?? {};
            sendResult(message.id, {
              schemaVersion: "1.0",
              receiptId: `browser-governance-showcase-prepare-review-${confirmed ? "review-required" : "prepared"}`,
              scenarioId: "browser-governance-showcase",
              actionId: "prepare-review",
              actionKind: "request_approval",
              actionClass: "send_message",
              label: "Prepare governance review",
              state: confirmed ? "review_required" : "prepared",
              summary: confirmed
                ? "The draft is ready for Site owner and governance administrator review."
                : "The draft is prepared for review.",
              requiresApproval: true,
              requiredApprover: "Site owner and governance administrator",
              submittedValues: Object.keys(formValues).map((fieldId) => ({
                fieldId,
                value: formValues[fieldId],
              })),
              nextSteps: ["Review the fields.", "Wait for authenticated confirmation."],
              executed: false,
              disclosure: "No enterprise action was executed.",
            });
          }
        }
      });
      iframe.srcdoc = html;
    },
    { html: widgetHtml, catalogPayload: catalog, experiencePayload: experience },
  );

  const widget = page.frameLocator("#employee-widget");
  await expect(widget.getByRole("heading", { name: "Choose a scenario" })).toBeVisible();
  await widget.getByRole("button", { name: "Open experience" }).click();
  await expect(widget.getByRole("heading", { name: experience.title })).toBeVisible();
  await expect(widget.locator("[data-section-kind]")).toHaveCount(16);

  await widget.getByRole("button", { name: "Prepare governance review" }).click();
  const preparedDialog = widget.getByRole("dialog", { name: "Prepare governance review" });
  await expect(preparedDialog).toContainText("Prepared");
  await expect(preparedDialog).toContainText("No enterprise action was executed");
  await widget.getByRole("button", { name: "Confirm and request review" }).click();
  await expect(widget.getByRole("dialog", { name: "Prepare governance review" })).toContainText(
    "Review Required",
  );

  const hostState = await page.evaluate(
    () =>
      (
        window as typeof window & {
          __employeeHost?: {
            initialized: boolean;
            messages: string[];
            protocolViolation: boolean;
            toolCalls: string[];
          };
        }
      ).__employeeHost,
  );
  expect(hostState?.protocolViolation).toBe(false);
  expect(hostState?.messages.slice(0, 3)).toEqual([
    "ui/initialize",
    "ui/notifications/initialized",
    "ui/notifications/size-changed",
  ]);
  expect(hostState?.toolCalls).toEqual([
    "open_employee_experience",
    "prepare_employee_action",
    "prepare_employee_action",
  ]);

  const overflow = await widget.locator("html").evaluate(
    (root) => root.scrollWidth > root.clientWidth + 1,
  );
  expect(overflow).toBe(false);
});
