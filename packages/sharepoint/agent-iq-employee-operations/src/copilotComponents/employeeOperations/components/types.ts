/* eslint-disable @rushstack/no-new-null */
import type {
  ICopilotComponentHostContext,
  ISPCopilotBridge,
  SPCopilotDisplayMode
} from '@microsoft/sp-copilot-component';

export type EmployeeAgentGroup =
  | 'line_of_business'
  | 'corporate_services'
  | 'management_governance';

export type ScenarioCategory =
  | 'command_center'
  | 'onboarding'
  | 'benefits'
  | 'payroll'
  | 'compensation'
  | 'time_off'
  | 'expense'
  | 'travel'
  | 'offboarding'
  | 'it_support'
  | 'security_access'
  | 'device_lifecycle'
  | 'workplace_wellness'
  | 'workplace_reservations'
  | 'recognition'
  | 'listening'
  | 'sales_customer'
  | 'finance_close'
  | 'procurement'
  | 'contractor'
  | 'news'
  | 'org_directory'
  | 'visual_qa'
  | 'learning'
  | 'site_provisioning'
  | 'site_governance';

export type ScenarioPhaseStatus =
  | 'completed'
  | 'in_progress'
  | 'needs_approval'
  | 'blocked'
  | 'pending';

export type ScenarioSectionKind =
  | 'dashboard'
  | 'timeline'
  | 'form'
  | 'comparison_table'
  | 'checklist'
  | 'chart'
  | 'radial_goal'
  | 'people_cards'
  | 'org_chart'
  | 'map'
  | 'gallery'
  | 'document_callouts'
  | 'decision_tree'
  | 'approvals'
  | 'policy_findings'
  | 'source_drawer'
  | 'follow_ups';

export type ScenarioActionKind =
  | 'open_draft'
  | 'request_approval'
  | 'send_message_draft'
  | 'schedule_draft'
  | 'view_source'
  | 'ask_follow_up'
  | 'export_summary'
  | 'escalate_draft';

export type ScenarioActionClass = 'local_ui' | 'send_message' | 'read_only_tool';

export interface EmployeeSelectedAgent {
  agentId: string;
  displayName: string;
  glyph: string;
  group: EmployeeAgentGroup;
  reason: string;
}

export interface ScenarioPhase {
  phaseId: string;
  agentId: string;
  title: string;
  status: ScenarioPhaseStatus;
  statusLabel: string;
  outputLabel: string;
  dependsOn: ReadonlyArray<string>;
}

export interface ScenarioMetric {
  metricId: string;
  label: string;
  value: string;
  trend: 'up' | 'down' | 'flat' | null;
  helperText: string | null;
}

export interface ScenarioSectionItem {
  itemId: string;
  label: string;
  value: string | null;
  description: string | null;
  state: string | null;
  href: string | null;
  metricValue: number | null;
  values: ReadonlyArray<string>;
  tags: ReadonlyArray<string>;
}

export interface ScenarioSection {
  sectionId: string;
  kind: ScenarioSectionKind;
  title: string;
  subtitle: string | null;
  columns: ReadonlyArray<string>;
  items: ReadonlyArray<ScenarioSectionItem>;
  footnote: string | null;
}

export interface ScenarioSource {
  sourceId: string;
  title: string;
  knowledgeSourceId: string;
  summary: string;
  url: string | null;
}

export interface ScenarioAction {
  actionId: string;
  kind: ScenarioActionKind;
  actionClass: ScenarioActionClass;
  label: string;
  description: string;
  requiresApproval: boolean;
}

export interface EmployeeExperience {
  schemaVersion: '1.0';
  scenarioId: string;
  category: ScenarioCategory;
  dataScope: string;
  readOnlyDisclosure: string;
  title: string;
  subtitle: string;
  status: ScenarioPhaseStatus;
  statusLabel: string;
  selectedAgents: ReadonlyArray<EmployeeSelectedAgent>;
  phases: ReadonlyArray<ScenarioPhase>;
  metrics: ReadonlyArray<ScenarioMetric>;
  sections: ReadonlyArray<ScenarioSection>;
  sources: ReadonlyArray<ScenarioSource>;
  actions: ReadonlyArray<ScenarioAction>;
  trustBoundary: string;
  suggestedPrompts: ReadonlyArray<string>;
}

export type EmployeeActionState = 'prepared' | 'review_required' | 'not_executed';

export interface EmployeeActionReceipt {
  receiptId: string;
  scenarioId: string;
  actionId: string;
  actionKind: ScenarioActionKind;
  actionClass: ScenarioActionClass;
  label: string;
  state: EmployeeActionState;
  summary: string;
  requiresApproval: boolean;
  requiredApprover: string | null;
  submittedValues: ReadonlyArray<{ fieldId: string; value: string }>;
  nextSteps: ReadonlyArray<string>;
  executed: false;
  disclosure: string;
}

export interface IEmployeeOperationsProps {
  experience: EmployeeExperience;
  allExperiences: ReadonlyArray<EmployeeExperience>;
  userDisplayName: string;
  siteTitle: string;
  serviceNotices: ReadonlyArray<string>;
  initialView: 'overview' | 'work' | 'sources';
  hostContext: ICopilotComponentHostContext;
  bridge: ISPCopilotBridge;
  onRequestDisplayMode: (mode: SPCopilotDisplayMode) => Promise<void>;
  onRequestSizeChange: (width: number, height: number) => Promise<void>;
  targetDocument: Document | undefined;
}
