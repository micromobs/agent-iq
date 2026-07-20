import { z } from 'zod';
import zodToJsonSchema from 'zod-to-json-schema';

const groupSchema = z.enum([
  'line_of_business',
  'corporate_services',
  'management_governance'
]);

const categorySchema = z.enum([
  'command_center',
  'onboarding',
  'benefits',
  'payroll',
  'compensation',
  'time_off',
  'expense',
  'travel',
  'offboarding',
  'it_support',
  'security_access',
  'device_lifecycle',
  'workplace_wellness',
  'workplace_reservations',
  'recognition',
  'listening',
  'sales_customer',
  'finance_close',
  'procurement',
  'contractor',
  'news',
  'org_directory',
  'visual_qa',
  'learning',
  'site_provisioning',
  'site_governance'
]);

const statusSchema = z.enum([
  'completed',
  'in_progress',
  'needs_approval',
  'blocked',
  'pending'
]);

const sectionKindSchema = z.enum([
  'dashboard',
  'timeline',
  'form',
  'comparison_table',
  'checklist',
  'chart',
  'radial_goal',
  'people_cards',
  'org_chart',
  'map',
  'gallery',
  'document_callouts',
  'decision_tree',
  'approvals',
  'policy_findings',
  'source_drawer',
  'follow_ups'
]);

const actionKindSchema = z.enum([
  'open_draft',
  'request_approval',
  'send_message_draft',
  'schedule_draft',
  'view_source',
  'ask_follow_up',
  'export_summary',
  'escalate_draft'
]);

const actionClassSchema = z.enum(['local_ui', 'send_message', 'read_only_tool']);

const selectedAgentSchema = z.object({
  agentId: z.string(),
  displayName: z.string(),
  glyph: z.string(),
  group: groupSchema,
  reason: z.string()
});

const phaseSchema = z.object({
  phaseId: z.string(),
  agentId: z.string(),
  title: z.string(),
  status: statusSchema,
  statusLabel: z.string(),
  outputLabel: z.string(),
  dependsOn: z.array(z.string())
});

const metricSchema = z.object({
  metricId: z.string(),
  label: z.string(),
  value: z.string(),
  trend: z.enum(['up', 'down', 'flat']).nullable(),
  helperText: z.string().nullable()
});

const itemSchema = z.object({
  itemId: z.string(),
  label: z.string(),
  value: z.string().nullable(),
  description: z.string().nullable(),
  state: z.string().nullable(),
  href: z.string().url().nullable(),
  metricValue: z.number().nullable(),
  values: z.array(z.string()),
  tags: z.array(z.string())
});

const sectionSchema = z.object({
  sectionId: z.string(),
  kind: sectionKindSchema,
  title: z.string(),
  subtitle: z.string().nullable(),
  columns: z.array(z.string()),
  items: z.array(itemSchema),
  footnote: z.string().nullable()
});

const sourceSchema = z.object({
  sourceId: z.string(),
  title: z.string(),
  knowledgeSourceId: z.string(),
  summary: z.string(),
  url: z.string().url().nullable()
});

const actionSchema = z.object({
  actionId: z.string(),
  kind: actionKindSchema,
  actionClass: actionClassSchema,
  label: z.string(),
  description: z.string(),
  requiresApproval: z.boolean()
});

export const employeeExperienceSchema = z.object({
  schemaVersion: z.literal('1.0'),
  scenarioId: z.string(),
  category: categorySchema,
  dataScope: z.string(),
  readOnlyDisclosure: z.string(),
  title: z.string(),
  subtitle: z.string(),
  status: statusSchema,
  statusLabel: z.string(),
  selectedAgents: z.array(selectedAgentSchema).min(2).max(6),
  phases: z.array(phaseSchema).min(2).max(10),
  metrics: z.array(metricSchema).min(1).max(6),
  sections: z.array(sectionSchema).min(3).max(6),
  sources: z.array(sourceSchema).min(3).max(6),
  actions: z.array(actionSchema).min(2).max(4),
  trustBoundary: z.string(),
  suggestedPrompts: z.array(z.string()).min(1).max(6)
});

const propertiesSchema = z.object({
  scenarioId: z.string().default('my-day').describe(
    'One of the 30 Agent IQ Employee Operations scenario IDs. Defaults to "my-day".'
  ),
  intent: z.string().optional().describe(
    'The employee-services intent that led to this experience. Used for display context only.'
  ),
  experience: employeeExperienceSchema.optional().describe(
    'Optional synthetic EmployeeExperience payload for isolated rendering tests. ' +
    'Omit this property in the pilot so the packaged employee-services catalog is used.'
  ),
  initialView: z.enum(['overview', 'work', 'sources']).default('overview').describe(
    'The initial fullscreen tab to emphasize.'
  )
});

export type IEmployeeOperationsCopilotComponentProperties = z.infer<typeof propertiesSchema>;

export default zodToJsonSchema(propertiesSchema);
