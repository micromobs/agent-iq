/* eslint-disable @rushstack/no-new-null */
import * as React from 'react';
import {
  Badge,
  Button,
  FluentProvider,
  IdPrefixProvider,
  MessageBar,
  MessageBarBody,
  Spinner,
  Tab,
  TabList,
  makeStyles,
  tokens,
  webDarkTheme,
  webLightTheme
} from '@fluentui/react-components';
import {
  ArrowExpand24Regular,
  ArrowMinimize24Regular,
  Chat24Regular,
  Open24Regular
} from '@fluentui/react-icons';
import { createCopilotTextContent } from '@microsoft/sp-copilot-component';

import type {
  EmployeeActionReceipt,
  EmployeeExperience,
  IEmployeeOperationsProps,
  ScenarioAction,
  ScenarioCategory,
  ScenarioPhaseStatus,
  ScenarioSection,
  ScenarioSectionItem
} from './types';

type ExperienceView = 'overview' | 'work' | 'sources';

const useStyles = makeStyles({
  root: {
    minHeight: '100%',
    color: tokens.colorNeutralForeground1,
    backgroundColor: tokens.colorNeutralBackground2,
    overflow: 'hidden'
  },
  shell: {
    display: 'flex',
    flexDirection: 'column',
    minHeight: '100%',
    backgroundImage:
      'radial-gradient(circle at 92% 3%, rgba(99,91,255,.12), transparent 30%), radial-gradient(circle at 1% 20%, rgba(0,139,139,.10), transparent 35%)'
  },
  topbar: {
    position: 'sticky',
    top: 0,
    zIndex: 10,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalM,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground1,
    boxShadow: tokens.shadow4
  },
  brand: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    minWidth: 0
  },
  brandMark: {
    width: '38px',
    height: '38px',
    flexShrink: 0,
    display: 'grid',
    placeItems: 'center',
    borderRadius: tokens.borderRadiusLarge,
    color: '#ffffff',
    fontWeight: 800,
    fontSize: tokens.fontSizeBase200,
    backgroundImage: 'linear-gradient(135deg, #0f6cbd, #635bff 58%, #008b8b)',
    boxShadow: '0 8px 24px rgba(15,108,189,.26)'
  },
  brandTitle: {
    fontWeight: 760,
    fontSize: tokens.fontSizeBase300,
    lineHeight: tokens.lineHeightBase300
  },
  brandSubtitle: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap'
  },
  topActions: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'flex-end',
    gap: tokens.spacingHorizontalXS,
    flexWrap: 'wrap'
  },
  body: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingHorizontalM,
    '@media (max-width: 640px)': {
      padding: tokens.spacingHorizontalS
    }
  },
  hero: {
    position: 'relative',
    overflow: 'hidden',
    display: 'grid',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingHorizontalXL,
    borderRadius: tokens.borderRadiusXLarge,
    color: '#ffffff',
    backgroundImage:
      'radial-gradient(circle at 87% 17%, rgba(255,255,255,.22), transparent 24%), linear-gradient(125deg, #073f78 0%, #4450a8 52%, #087d7b 100%)',
    boxShadow: '0 18px 44px rgba(25,71,130,.22)',
    '@media (max-width: 640px)': {
      padding: tokens.spacingHorizontalL
    }
  },
  heroMeta: {
    display: 'flex',
    gap: tokens.spacingHorizontalXS,
    flexWrap: 'wrap'
  },
  heroTitle: {
    margin: 0,
    maxWidth: '880px',
    fontSize: 'clamp(26px, 5vw, 44px)',
    lineHeight: 1.04,
    letterSpacing: '-.025em'
  },
  heroSubtitle: {
    margin: 0,
    maxWidth: '820px',
    color: 'rgba(255,255,255,.84)',
    fontSize: tokens.fontSizeBase300,
    lineHeight: tokens.lineHeightBase400
  },
  heroContext: {
    color: 'rgba(255,255,255,.72)',
    fontSize: tokens.fontSizeBase100
  },
  metrics: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))',
    gap: tokens.spacingHorizontalS
  },
  metric: {
    minWidth: 0,
    padding: tokens.spacingHorizontalM,
    border: '1px solid rgba(255,255,255,.22)',
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: 'rgba(255,255,255,.11)',
    backdropFilter: 'blur(8px)'
  },
  metricLabel: {
    color: 'rgba(255,255,255,.72)',
    fontSize: tokens.fontSizeBase100
  },
  metricValue: {
    marginTop: tokens.spacingVerticalXXS,
    fontSize: tokens.fontSizeHero800,
    fontWeight: 760,
    lineHeight: 1
  },
  metricHelper: {
    marginTop: tokens.spacingVerticalXS,
    color: 'rgba(255,255,255,.7)',
    fontSize: tokens.fontSizeBase100
  },
  panel: {
    minWidth: 0,
    padding: tokens.spacingHorizontalM,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusXLarge,
    backgroundColor: tokens.colorNeutralBackground1,
    boxShadow: tokens.shadow4
  },
  panelHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalM,
    marginBottom: tokens.spacingVerticalM
  },
  panelTitle: {
    margin: 0,
    fontSize: tokens.fontSizeBase500,
    lineHeight: tokens.lineHeightBase500
  },
  panelSubtitle: {
    marginTop: tokens.spacingVerticalXXS,
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200
  },
  tabRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalM,
    flexWrap: 'wrap'
  },
  scenarioSelect: {
    minWidth: '230px',
    minHeight: '36px',
    padding: `0 ${tokens.spacingHorizontalS}`,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusMedium,
    color: tokens.colorNeutralForeground1,
    backgroundColor: tokens.colorNeutralBackground1
  },
  stageRail: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))',
    gap: tokens.spacingHorizontalS
  },
  stage: {
    position: 'relative',
    minHeight: '112px',
    padding: tokens.spacingHorizontalM,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1
  },
  stageCompleted: {
    borderTop: `4px solid ${tokens.colorPaletteGreenBorderActive}`
  },
  stageInProgress: {
    borderTop: `4px solid ${tokens.colorBrandStroke1}`
  },
  stageNeedsApproval: {
    borderTop: `4px solid ${tokens.colorPaletteMarigoldBorderActive}`
  },
  stageBlocked: {
    borderTop: `4px solid ${tokens.colorPaletteRedBorderActive}`
  },
  stagePending: {
    borderTop: `4px solid ${tokens.colorNeutralStroke1}`
  },
  stageTitle: {
    marginTop: tokens.spacingVerticalS,
    fontWeight: 720,
    fontSize: tokens.fontSizeBase200
  },
  stageOutput: {
    marginTop: tokens.spacingVerticalXS,
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
    lineHeight: tokens.lineHeightBase200
  },
  agentGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
    gap: tokens.spacingHorizontalS
  },
  agentCard: {
    display: 'grid',
    gridTemplateColumns: '44px minmax(0, 1fr)',
    gap: tokens.spacingHorizontalS,
    padding: tokens.spacingHorizontalM,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1
  },
  agentGlyph: {
    width: '44px',
    height: '44px',
    display: 'grid',
    placeItems: 'center',
    borderRadius: tokens.borderRadiusLarge,
    color: '#ffffff',
    fontSize: tokens.fontSizeBase100,
    fontWeight: 800,
    backgroundImage: 'linear-gradient(135deg, #0f6cbd, #635bff)'
  },
  agentName: {
    fontWeight: 720,
    fontSize: tokens.fontSizeBase200
  },
  agentReason: {
    marginTop: tokens.spacingVerticalXXS,
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
    lineHeight: tokens.lineHeightBase200
  },
  sectionGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(12, minmax(0, 1fr))',
    gap: tokens.spacingHorizontalM
  },
  halfSection: {
    gridColumn: 'span 6',
    '@media (max-width: 900px)': {
      gridColumn: '1 / -1'
    }
  },
  wideSection: {
    gridColumn: '1 / -1'
  },
  tileGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(135px, 1fr))',
    gap: tokens.spacingHorizontalS
  },
  tile: {
    padding: tokens.spacingHorizontalM,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1
  },
  tileValue: {
    fontSize: tokens.fontSizeHero700,
    fontWeight: 760,
    lineHeight: 1
  },
  smallMuted: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
    lineHeight: tokens.lineHeightBase200
  },
  timeline: {
    display: 'grid',
    gap: 0
  },
  timelineRow: {
    display: 'grid',
    gridTemplateColumns: '20px minmax(0, 1fr)',
    gap: tokens.spacingHorizontalS,
    minHeight: '58px'
  },
  timelineDot: {
    width: '11px',
    height: '11px',
    marginTop: tokens.spacingVerticalXS,
    borderRadius: '50%',
    backgroundColor: tokens.colorBrandBackground,
    boxShadow: `0 0 0 4px ${tokens.colorBrandBackground2}`
  },
  timelineLabel: {
    fontWeight: 700,
    fontSize: tokens.fontSizeBase200
  },
  formGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
    gap: tokens.spacingHorizontalM,
    '@media (max-width: 640px)': {
      gridTemplateColumns: '1fr'
    }
  },
  field: {
    display: 'grid',
    gap: tokens.spacingVerticalXS
  },
  fieldLabel: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
    fontWeight: 650
  },
  fieldInput: {
    width: '100%',
    minHeight: '38px',
    padding: `0 ${tokens.spacingHorizontalS}`,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusMedium,
    color: tokens.colorNeutralForeground1,
    backgroundColor: tokens.colorNeutralBackground1
  },
  tableWrap: {
    overflowX: 'auto',
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge
  },
  table: {
    width: '100%',
    minWidth: '520px',
    borderCollapse: 'collapse'
  },
  tableCell: {
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    textAlign: 'left',
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    fontSize: tokens.fontSizeBase200
  },
  tableHead: {
    color: tokens.colorNeutralForeground3,
    backgroundColor: tokens.colorNeutralBackground3
  },
  checklist: {
    display: 'grid',
    gap: tokens.spacingVerticalS,
    margin: 0,
    padding: 0,
    listStyle: 'none'
  },
  checkRow: {
    display: 'grid',
    gridTemplateColumns: '26px minmax(0, 1fr) auto',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS
  },
  checkIcon: {
    width: '24px',
    height: '24px',
    display: 'grid',
    placeItems: 'center',
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: '50%',
    fontSize: tokens.fontSizeBase100,
    fontWeight: 800
  },
  chart: {
    display: 'grid',
    gap: tokens.spacingVerticalS
  },
  chartRow: {
    display: 'grid',
    gridTemplateColumns: 'minmax(90px, .8fr) minmax(130px, 2fr) 48px',
    gap: tokens.spacingHorizontalS,
    alignItems: 'center'
  },
  chartTrack: {
    height: '12px',
    overflow: 'hidden',
    borderRadius: tokens.borderRadiusCircular,
    backgroundColor: tokens.colorNeutralBackground3
  },
  chartFill: {
    height: '100%',
    borderRadius: tokens.borderRadiusCircular,
    backgroundImage: 'linear-gradient(90deg, #0f6cbd, #635bff, #008b8b)'
  },
  radialGoal: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXL
  },
  radialRing: {
    width: '132px',
    height: '132px',
    flex: '0 0 132px',
    display: 'grid',
    placeItems: 'center',
    borderRadius: '50%'
  },
  radialCenter: {
    width: '96px',
    height: '96px',
    display: 'grid',
    placeItems: 'center',
    textAlign: 'center',
    borderRadius: '50%',
    backgroundColor: tokens.colorNeutralBackground1
  },
  radialValue: {
    color: tokens.colorNeutralForeground1,
    fontSize: tokens.fontSizeHero800,
    fontWeight: 800,
    lineHeight: 1
  },
  radialLegend: {
    minWidth: '180px',
    display: 'grid',
    gridTemplateColumns: 'repeat(2, minmax(72px, 1fr))',
    gap: tokens.spacingVerticalS
  },
  radialLegendItem: {
    display: 'grid',
    gap: tokens.spacingVerticalXXS
  },
  cardsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
    gap: tokens.spacingHorizontalS
  },
  miniCard: {
    padding: tokens.spacingHorizontalM,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1
  },
  avatar: {
    width: '38px',
    height: '38px',
    display: 'grid',
    placeItems: 'center',
    borderRadius: '50%',
    color: '#ffffff',
    fontSize: tokens.fontSizeBase100,
    fontWeight: 800,
    backgroundImage: 'linear-gradient(135deg, #635bff, #008b8b)'
  },
  cardHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS
  },
  orgChart: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingHorizontalM,
    flexWrap: 'wrap'
  },
  orgArrow: {
    color: tokens.colorNeutralForeground3,
    fontWeight: 800
  },
  map: {
    position: 'relative',
    minHeight: '270px',
    overflow: 'hidden',
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusXLarge,
    backgroundColor: tokens.colorNeutralBackground3,
    backgroundImage:
      'linear-gradient(90deg, transparent 48%, rgba(128,128,128,.12) 49%, transparent 51%), linear-gradient(0deg, transparent 48%, rgba(128,128,128,.12) 49%, transparent 51%)',
    backgroundSize: '54px 54px'
  },
  mapPin: {
    position: 'absolute',
    maxWidth: '160px',
    padding: tokens.spacingHorizontalS,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
    boxShadow: tokens.shadow8
  },
  galleryVisual: {
    height: '88px',
    display: 'grid',
    placeItems: 'center',
    margin: `-${tokens.spacingHorizontalM} -${tokens.spacingHorizontalM} ${tokens.spacingVerticalS}`,
    borderRadius: `${tokens.borderRadiusLarge} ${tokens.borderRadiusLarge} 0 0`,
    color: '#ffffff',
    fontSize: tokens.fontSizeHero700,
    fontWeight: 800,
    backgroundImage:
      'radial-gradient(circle at 73% 22%, rgba(255,255,255,.32), transparent 20%), linear-gradient(135deg, #0f6cbd, #635bff 55%, #008b8b)'
  },
  callout: {
    padding: tokens.spacingHorizontalM,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderLeft: `4px solid ${tokens.colorBrandStroke1}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1
  },
  warningCallout: {
    borderLeftColor: tokens.colorPaletteMarigoldBorderActive
  },
  dangerCallout: {
    borderLeftColor: tokens.colorPaletteRedBorderActive
  },
  successCallout: {
    borderLeftColor: tokens.colorPaletteGreenBorderActive
  },
  sourceList: {
    display: 'grid',
    gap: tokens.spacingVerticalS
  },
  sourceRow: {
    display: 'grid',
    gridTemplateColumns: '30px minmax(0, 1fr) auto',
    gap: tokens.spacingHorizontalS,
    alignItems: 'start',
    padding: tokens.spacingHorizontalS,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1
  },
  sourceNumber: {
    width: '28px',
    height: '28px',
    display: 'grid',
    placeItems: 'center',
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground3,
    fontWeight: 800,
    fontSize: tokens.fontSizeBase100
  },
  actionDock: {
    position: 'sticky',
    bottom: 0,
    zIndex: 9,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalM,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground1,
    boxShadow: tokens.shadow16,
    '@media (max-width: 760px)': {
      alignItems: 'stretch',
      flexDirection: 'column'
    }
  },
  actionButtons: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: tokens.spacingHorizontalXS,
    flexWrap: 'wrap'
  },
  trust: {
    display: 'grid',
    gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
    gap: tokens.spacingHorizontalS,
    padding: tokens.spacingHorizontalM,
    border: `1px solid ${tokens.colorBrandStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    color: tokens.colorNeutralForeground3,
    backgroundColor: tokens.colorBrandBackground2,
    fontSize: tokens.fontSizeBase100,
    '@media (max-width: 640px)': {
      gridTemplateColumns: '1fr'
    }
  },
  receipt: {
    position: 'fixed',
    inset: 0,
    zIndex: 100,
    display: 'grid',
    placeItems: 'center',
    padding: tokens.spacingHorizontalM,
    backgroundColor: 'rgba(5,10,20,.58)',
    backdropFilter: 'blur(8px)'
  },
  receiptCard: {
    width: 'min(620px, 100%)',
    maxHeight: '88vh',
    overflowY: 'auto',
    padding: tokens.spacingHorizontalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusXLarge,
    backgroundColor: tokens.colorNeutralBackground1,
    boxShadow: tokens.shadow64
  },
  receiptState: {
    margin: `${tokens.spacingVerticalM} 0`,
    padding: tokens.spacingHorizontalM,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground3
  },
  receiptActions: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: tokens.spacingHorizontalS,
    marginTop: tokens.spacingVerticalM,
    flexWrap: 'wrap'
  },
  notices: {
    display: 'grid',
    gap: tokens.spacingVerticalXS
  }
});

const WIDE_SECTION_KINDS = new Set([
  'comparison_table',
  'org_chart',
  'map',
  'policy_findings',
  'source_drawer'
]);

const APPROVER_BY_CATEGORY: Record<ScenarioCategory, string> = {
  command_center: 'Employee',
  onboarding: 'Hiring manager and service owner',
  benefits: 'Benefits administrator',
  payroll: 'Payroll specialist',
  compensation: 'Manager and HR business partner',
  time_off: 'Manager',
  expense: 'Expense approver',
  travel: 'Travel approver',
  offboarding: 'Manager, HR, and IT service owner',
  it_support: 'IT service owner',
  security_access: 'Resource owner and IT Security',
  device_lifecycle: 'IT asset manager',
  workplace_wellness: 'Employee',
  workplace_reservations: 'Workplace services',
  recognition: 'Employee',
  listening: 'People manager',
  sales_customer: 'Account owner',
  finance_close: 'Finance control owner',
  procurement: 'Procurement approver',
  contractor: 'Hiring manager and access owner',
  news: 'Employee',
  org_directory: 'Employee',
  visual_qa: 'Policy owner',
  learning: 'Manager',
  site_provisioning: 'SharePoint administrator',
  site_governance: 'Site owner and governance administrator'
};

function categoryLabel(value: string): string {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function initials(label: string): string {
  return label
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0))
    .join('')
    .toUpperCase();
}

function statusColor(status: ScenarioPhaseStatus): 'success' | 'warning' | 'danger' | 'informative' {
  if (status === 'completed') return 'success';
  if (status === 'needs_approval') return 'warning';
  if (status === 'blocked') return 'danger';
  return 'informative';
}

function stageClass(
  styles: ReturnType<typeof useStyles>,
  status: ScenarioPhaseStatus
): string {
  const byStatus: Record<ScenarioPhaseStatus, string> = {
    completed: styles.stageCompleted,
    in_progress: styles.stageInProgress,
    needs_approval: styles.stageNeedsApproval,
    blocked: styles.stageBlocked,
    pending: styles.stagePending
  };
  return `${styles.stage} ${byStatus[status]}`;
}

export default function EmployeeOperations(props: IEmployeeOperationsProps): React.ReactElement {
  const styles = useStyles();
  const [providerVersion, setProviderVersion] = React.useState<number>(0);
  const [experience, setExperience] = React.useState<EmployeeExperience>(props.experience);
  const [activeView, setActiveView] = React.useState<ExperienceView>(props.initialView);
  const [formValues, setFormValues] = React.useState<Record<string, string>>({});
  const [receipt, setReceipt] = React.useState<EmployeeActionReceipt | null>(null);
  const [interactionNotice, setInteractionNotice] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<boolean>(false);

  React.useEffect(() => {
    setProviderVersion(1);
  }, []);

  React.useEffect(() => {
    setExperience(props.experience);
    setActiveView(props.initialView);
  }, [props.experience, props.initialView]);

  React.useEffect(() => {
    const defaults: Record<string, string> = {};
    for (const section of experience.sections) {
      if (section.kind !== 'form') continue;
      for (const item of section.items) defaults[item.itemId] = item.value ?? '';
    }
    setFormValues(defaults);
    setReceipt(null);
    setInteractionNotice(null);
  }, [experience]);

  const fullscreen = props.hostContext.displayMode === 'fullscreen';
  const theme = props.hostContext.theme === 'dark' ? webDarkTheme : webLightTheme;
  const visibleSections = fullscreen
    ? experience.sections
    : experience.sections.filter((section) => section.kind !== 'source_drawer').slice(0, 3);

  const handleDisplayMode = React.useCallback(async (): Promise<void> => {
    const target = fullscreen ? 'inline' : 'fullscreen';
    try {
      await props.onRequestDisplayMode(target);
      setInteractionNotice(target === 'fullscreen' ? 'Expanded to fullscreen.' : 'Returned inline.');
    } catch (error: unknown) {
      setInteractionNotice(`Display mode change failed: ${errorMessage(error)}`);
    }
  }, [fullscreen, props]);

  const sendFollowUp = React.useCallback(async (prompt: string): Promise<void> => {
    setBusy(true);
    try {
      await props.bridge.sendFollowUpMessageAsync([createCopilotTextContent(prompt)]);
      setInteractionNotice('Follow-up sent to Copilot.');
    } catch (error: unknown) {
      setInteractionNotice(`Copilot did not accept the follow-up: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  }, [props.bridge]);

  const openLink = React.useCallback(async (url: string | null): Promise<void> => {
    setInteractionNotice(
      url
        ? 'External links are disabled in this disconnected synthetic preview.'
        : 'This synthetic source does not have an external link.'
    );
  }, []);

  const changeScenario = React.useCallback((scenarioId: string): void => {
    const candidate = props.allExperiences.find((item) => item.scenarioId === scenarioId);
    if (candidate) setExperience(candidate);
  }, [props.allExperiences]);

  const prepareAction = React.useCallback((action: ScenarioAction): void => {
    if (action.kind === 'view_source') {
      setActiveView('sources');
      setInteractionNotice('Source view selected.');
      return;
    }
    if (action.kind === 'ask_follow_up') {
      sendFollowUp(experience.suggestedPrompts[0] ?? action.description).catch((error: unknown) => {
        setInteractionNotice(`Copilot follow-up failed: ${errorMessage(error)}`);
      });
      return;
    }
    if (action.kind === 'export_summary') {
      sendFollowUp(`Create a copyable summary of the ${experience.title} experience.`).catch(
        (error: unknown) => {
          setInteractionNotice(`Copilot summary request failed: ${errorMessage(error)}`);
        }
      );
      return;
    }

    const approver = APPROVER_BY_CATEGORY[experience.category];
    setReceipt({
      receiptId: `${experience.scenarioId}-${action.actionId}-prepared`,
      scenarioId: experience.scenarioId,
      actionId: action.actionId,
      actionKind: action.kind,
      actionClass: action.actionClass,
      label: action.label,
      state: 'prepared',
      summary:
        `The "${action.label}" draft is prepared for review. ` +
        'No enterprise system has been changed.',
      requiresApproval: action.requiresApproval,
      requiredApprover: approver,
      submittedValues: Object.keys(formValues).map((fieldId) => ({
        fieldId,
        value: formValues[fieldId]
      })),
      nextSteps: [
        'Review every prepared field.',
        `Confirm when ready to route the draft to ${approver}.`
      ],
      executed: false,
      disclosure: experience.readOnlyDisclosure
    });
  }, [experience, formValues, sendFollowUp]);

  const confirmReceipt = React.useCallback(async (): Promise<void> => {
    if (!receipt) return;
    const approver = receipt.requiredApprover ?? 'the named approver';
    const reviewed: EmployeeActionReceipt = {
      ...receipt,
      receiptId: `${receipt.scenarioId}-${receipt.actionId}-review-required`,
      state: 'review_required',
      summary:
        `The "${receipt.label}" draft is ready for ${approver} review. ` +
        'It has not been submitted to a system of record.',
      nextSteps: [
        `Route the draft to ${approver}.`,
        'Wait for authenticated system-of-record confirmation.',
        'Do not treat this receipt as proof of execution.'
      ],
      executed: false
    };
    setReceipt(reviewed);
    await sendFollowUp(
      `Review the prepared "${receipt.label}" draft for the ${experience.title} scenario. ` +
      `Required approver: ${approver}. Do not claim execution.`
    );
  }, [experience.title, receipt, sendFollowUp]);

  const renderSection = (section: ScenarioSection): React.ReactElement => {
    const className = WIDE_SECTION_KINDS.has(section.kind)
      ? `${styles.panel} ${styles.wideSection}`
      : `${styles.panel} ${styles.halfSection}`;
    return (
      <section className={className} key={section.sectionId} data-section-kind={section.kind}>
        <div className={styles.panelHeader}>
          <div>
            <h2 className={styles.panelTitle}>{section.title}</h2>
            {section.subtitle && <div className={styles.panelSubtitle}>{section.subtitle}</div>}
          </div>
          <Badge appearance="outline" color="informative">{categoryLabel(section.kind)}</Badge>
        </div>
        {renderSectionBody(
          section,
          styles,
          formValues,
          setFormValues,
          openLink,
          sendFollowUp
        )}
        {section.footnote && <div className={styles.smallMuted}>{section.footnote}</div>}
      </section>
    );
  };

  return (
    <IdPrefixProvider value="agent-iq-employee-operations-">
      <FluentProvider
        key={providerVersion}
        theme={theme}
        targetDocument={props.targetDocument}
        className={styles.root}
      >
        <div className={styles.shell}>
          <header className={styles.topbar}>
            <div className={styles.brand}>
              <div className={styles.brandMark} aria-hidden="true">IQ</div>
              <div>
                <div className={styles.brandTitle}>Agent IQ Employee Operations</div>
                <div className={styles.brandSubtitle}>
                  {experience.selectedAgents.length} specialists for {experience.title}
                </div>
              </div>
            </div>
            <div className={styles.topActions}>
              <Badge appearance="outline" color="informative">Read-only</Badge>
              <Button
                appearance="subtle"
                icon={fullscreen ? <ArrowMinimize24Regular /> : <ArrowExpand24Regular />}
                onClick={handleDisplayMode}
              >
                {fullscreen ? 'Return inline' : 'Expand'}
              </Button>
            </div>
          </header>

          <main className={styles.body}>
            {props.serviceNotices.length > 0 && (
              <div className={styles.notices}>
                {props.serviceNotices.map((notice) => (
                  <MessageBar key={notice} intent="warning">
                    <MessageBarBody>{notice}</MessageBarBody>
                  </MessageBar>
                ))}
              </div>
            )}
            {interactionNotice && (
              <MessageBar intent="info">
                <MessageBarBody>{interactionNotice}</MessageBarBody>
              </MessageBar>
            )}

            <section className={styles.hero}>
              <div className={styles.heroMeta}>
                <Badge appearance="filled" color={statusColor(experience.status)}>
                  {experience.statusLabel}
                </Badge>
                <Badge appearance="outline">{categoryLabel(experience.category)}</Badge>
                <Badge appearance="outline">{experience.sources.length} cited sources</Badge>
              </div>
              <div>
                <h1 className={styles.heroTitle}>{experience.title}</h1>
                <p className={styles.heroSubtitle}>{experience.subtitle}</p>
                <div className={styles.heroContext}>
                  Hello {props.userDisplayName}. Context: {props.siteTitle}.
                </div>
              </div>
              <div className={styles.metrics}>
                {experience.metrics.map((metric) => (
                  <div className={styles.metric} key={metric.metricId}>
                    <div className={styles.metricLabel}>{metric.label}</div>
                    <div className={styles.metricValue}>{metric.value}</div>
                    <div className={styles.metricHelper}>
                      {metric.trend ? `${metric.trend.toUpperCase()} trend` : metric.helperText}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className={styles.panel}>
              <div className={styles.tabRow}>
                <TabList
                  selectedValue={activeView}
                  onTabSelect={(_, data) => setActiveView(data.value as ExperienceView)}
                >
                  <Tab value="overview">Experience</Tab>
                  <Tab value="work">Show work</Tab>
                  <Tab value="sources">Sources</Tab>
                </TabList>
                <select
                  className={styles.scenarioSelect}
                  aria-label="Choose Employee Operations scenario"
                  value={experience.scenarioId}
                  onChange={(event) => changeScenario(event.currentTarget.value)}
                >
                  {props.allExperiences.map((candidate) => (
                    <option key={candidate.scenarioId} value={candidate.scenarioId}>
                      {candidate.title}
                    </option>
                  ))}
                </select>
              </div>
            </section>

            {activeView === 'overview' && (
              <>
                <section className={styles.panel}>
                  <div className={styles.panelHeader}>
                    <div>
                      <h2 className={styles.panelTitle}>Observable work</h2>
                      <div className={styles.panelSubtitle}>
                        Structured specialist stages, not private model reasoning.
                      </div>
                    </div>
                    <Badge appearance="outline" color="informative">
                      {experience.phases.length} stages
                    </Badge>
                  </div>
                  <div className={styles.stageRail} aria-label="Observable work stages">
                    {experience.phases.map((phase) => (
                      <article className={stageClass(styles, phase.status)} key={phase.phaseId}>
                        <Badge appearance="outline" color={statusColor(phase.status)}>
                          {phase.statusLabel}
                        </Badge>
                        <div className={styles.stageTitle}>{phase.title}</div>
                        <div className={styles.stageOutput}>{phase.outputLabel}</div>
                      </article>
                    ))}
                  </div>
                </section>
                <div className={styles.sectionGrid}>
                  {visibleSections.map(renderSection)}
                </div>
              </>
            )}

            {activeView === 'work' && (
              <>
                <section className={styles.panel}>
                  <div className={styles.panelHeader}>
                    <div>
                      <h2 className={styles.panelTitle}>Selected specialist squad</h2>
                      <div className={styles.panelSubtitle}>
                        Public selection reasons and ownership for each stage.
                      </div>
                    </div>
                    <Badge appearance="outline" color="informative">
                      {experience.selectedAgents.length} agents
                    </Badge>
                  </div>
                  <div className={styles.agentGrid}>
                    {experience.selectedAgents.map((agent) => (
                      <article className={styles.agentCard} key={agent.agentId}>
                        <div className={styles.agentGlyph}>{agent.glyph}</div>
                        <div>
                          <div className={styles.agentName}>{agent.displayName}</div>
                          <div className={styles.agentReason}>{agent.reason}</div>
                          <Badge appearance="outline">{categoryLabel(agent.group)}</Badge>
                        </div>
                      </article>
                    ))}
                  </div>
                </section>
                <section className={styles.panel}>
                  <div className={styles.panelHeader}>
                    <div>
                      <h2 className={styles.panelTitle}>Synthetic workflow graph</h2>
                      <div className={styles.panelSubtitle}>
                        Dependencies are authored and acyclic; raw tool arguments and chain-of-thought are omitted.
                      </div>
                    </div>
                  </div>
                  <div className={styles.stageRail}>
                    {experience.phases.map((phase) => (
                      <article className={stageClass(styles, phase.status)} key={phase.phaseId}>
                        <Badge appearance="outline" color={statusColor(phase.status)}>
                          {phase.statusLabel}
                        </Badge>
                        <div className={styles.stageTitle}>{phase.title}</div>
                        <div className={styles.stageOutput}>{phase.outputLabel}</div>
                        {phase.dependsOn.length > 0 && (
                          <div className={styles.smallMuted}>
                            After: {phase.dependsOn.join(', ')}
                          </div>
                        )}
                      </article>
                    ))}
                  </div>
                </section>
              </>
            )}

            {activeView === 'sources' && (
              <section className={styles.panel}>
                <div className={styles.panelHeader}>
                  <div>
                    <h2 className={styles.panelTitle}>Grounded sources</h2>
                    <div className={styles.panelSubtitle}>
                      Packaged synthetic source names and summaries. External links are disabled.
                    </div>
                  </div>
                  <Badge appearance="outline" color="informative">
                    {experience.sources.length} sources
                  </Badge>
                </div>
                <div className={styles.sourceList}>
                  {experience.sources.map((source, index) => (
                    <div className={styles.sourceRow} key={source.sourceId}>
                      <div className={styles.sourceNumber}>{index + 1}</div>
                      <div>
                        <div className={styles.agentName}>{source.title}</div>
                        <div className={styles.smallMuted}>{source.summary}</div>
                        <div className={styles.smallMuted}>
                          Knowledge source: {source.knowledgeSourceId}
                        </div>
                      </div>
                      <Button
                        appearance="subtle"
                        icon={<Open24Regular />}
                        disabled={!source.url}
                        onClick={() => openLink(source.url)}
                      >
                        Open
                      </Button>
                    </div>
                  ))}
                </div>
              </section>
            )}

            <section className={styles.trust}>
              <div>
                <strong>Evidence scope</strong>
                <div>{experience.dataScope}</div>
              </div>
              <div>
                <strong>Action boundary</strong>
                <div>{experience.trustBoundary}</div>
              </div>
            </section>
          </main>

          <footer className={styles.actionDock} aria-label="Scenario actions">
            <div>
              <div className={styles.agentName}>Take the next safe step</div>
              <div className={styles.smallMuted}>
                Actions prepare drafts, follow-ups, or review requests. No enterprise mutation is claimed.
              </div>
            </div>
            <div className={styles.actionButtons}>
              {busy && <Spinner size="tiny" label="Working" />}
              {experience.actions.map((action, index) => (
                <Button
                  key={action.actionId}
                  appearance={index === experience.actions.length - 1 ? 'primary' : 'subtle'}
                  icon={action.kind === 'ask_follow_up' ? <Chat24Regular /> : undefined}
                  disabled={busy}
                  title={action.description}
                  onClick={() => prepareAction(action)}
                >
                  {action.label}
                </Button>
              ))}
            </div>
          </footer>

          {receipt && (
            <div className={styles.receipt} role="dialog" aria-modal="true" aria-labelledby="receipt-title">
              <section className={styles.receiptCard}>
                <Badge
                  appearance="filled"
                  color={receipt.state === 'review_required' ? 'warning' : 'informative'}
                >
                  {categoryLabel(receipt.state)}
                </Badge>
                <h2 id="receipt-title">{receipt.label}</h2>
                <div className={styles.receiptState}>{receipt.summary}</div>
                {receipt.requiredApprover && (
                  <p><strong>Required approver:</strong> {receipt.requiredApprover}</p>
                )}
                {receipt.submittedValues.length > 0 && (
                  <>
                    <h3>Prepared fields</h3>
                    <ul>
                      {receipt.submittedValues.map((item) => (
                        <li key={item.fieldId}>{item.fieldId}: {item.value}</li>
                      ))}
                    </ul>
                  </>
                )}
                <h3>Next steps</h3>
                <ol>
                  {receipt.nextSteps.map((step) => <li key={step}>{step}</li>)}
                </ol>
                <MessageBar intent="warning">
                  <MessageBarBody>No enterprise action was executed.</MessageBarBody>
                </MessageBar>
                <div className={styles.receiptActions}>
                  <Button appearance="subtle" onClick={() => setReceipt(null)}>Close</Button>
                  {receipt.state === 'prepared' && (
                    <Button appearance="primary" onClick={confirmReceipt}>
                      Confirm and request review
                    </Button>
                  )}
                </div>
              </section>
            </div>
          )}
        </div>
      </FluentProvider>
    </IdPrefixProvider>
  );
}

function renderSectionBody(
  section: ScenarioSection,
  styles: ReturnType<typeof useStyles>,
  formValues: Record<string, string>,
  setFormValues: React.Dispatch<React.SetStateAction<Record<string, string>>>,
  openLink: (url: string | null) => Promise<void>,
  sendFollowUp: (prompt: string) => Promise<void>
): React.ReactElement {
  if (section.kind === 'dashboard') {
    return (
      <div className={styles.tileGrid}>
        {section.items.map((item) => (
          <div className={styles.tile} key={item.itemId}>
            <div className={styles.tileValue}>{item.value}</div>
            <div className={styles.agentName}>{item.label}</div>
            {item.description && <div className={styles.smallMuted}>{item.description}</div>}
          </div>
        ))}
      </div>
    );
  }

  if (section.kind === 'timeline') {
    return (
      <div className={styles.timeline}>
        {section.items.map((item) => (
          <div className={styles.timelineRow} key={item.itemId}>
            <div className={styles.timelineDot} />
            <div>
              <div className={styles.timelineLabel}>
                {item.label} {item.state && <Badge appearance="outline">{categoryLabel(item.state)}</Badge>}
              </div>
              {item.description && <div className={styles.smallMuted}>{item.description}</div>}
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (section.kind === 'form') {
    return (
      <div className={styles.formGrid}>
        {section.items.map((item) => (
          <label className={styles.field} key={item.itemId}>
            <span className={styles.fieldLabel}>{item.label}</span>
            <input
              className={styles.fieldInput}
              value={formValues[item.itemId] ?? item.value ?? ''}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setFormValues((current) => ({ ...current, [item.itemId]: value }));
              }}
            />
          </label>
        ))}
      </div>
    );
  }

  if (section.kind === 'comparison_table') {
    return (
      <div
        aria-label={`${section.title} comparison table`}
        className={styles.tableWrap}
        role="region"
        tabIndex={0}
      >
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={`${styles.tableCell} ${styles.tableHead}`}>Criteria</th>
              {section.columns.map((column) => (
                <th className={`${styles.tableCell} ${styles.tableHead}`} key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {section.items.map((item) => (
              <tr key={item.itemId}>
                <td className={styles.tableCell}>{item.label}</td>
                {item.values.map((value, index) => (
                  <td className={styles.tableCell} key={`${item.itemId}-${section.columns[index]}`}>
                    {value}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (section.kind === 'checklist') {
    return (
      <ul className={styles.checklist}>
        {section.items.map((item) => (
          <li className={styles.checkRow} key={item.itemId}>
            <span className={styles.checkIcon}>
              {item.state === 'done' ? 'OK' : item.state === 'blocked' ? '!' : ''}
            </span>
            <span>{item.label}</span>
            <Badge appearance="outline">{categoryLabel(item.state ?? 'pending')}</Badge>
          </li>
        ))}
      </ul>
    );
  }

  if (section.kind === 'chart') {
    const maximum = Math.max(...section.items.map((item) => item.metricValue ?? 0), 1);
    return (
      <div className={styles.chart}>
        {section.items.map((item) => (
          <div className={styles.chartRow} key={item.itemId}>
            <div className={styles.smallMuted}>{item.label}</div>
            <div className={styles.chartTrack}>
              <div
                className={styles.chartFill}
                style={{ width: `${Math.max(2, ((item.metricValue ?? 0) / maximum) * 100)}%` }}
              />
            </div>
            <div className={styles.smallMuted}>{item.value ?? item.metricValue}</div>
          </div>
        ))}
      </div>
    );
  }

  if (section.kind === 'radial_goal') {
    const values = new Map(
      section.items.map((item) => [item.itemId, item.metricValue ?? 0])
    );
    const entitlement = Math.max(values.get('entitlement') ?? 0, 1);
    const remaining = Math.max(values.get('remaining') ?? 0, 0);
    const usedPercent = Math.min(100, Math.max(0, ((entitlement - remaining) / entitlement) * 100));
    return (
      <div className={styles.radialGoal}>
        <div
          aria-label={`${remaining} vacation days remaining out of ${entitlement}`}
          className={styles.radialRing}
          role="img"
          style={{
            background: `conic-gradient(#635bff 0 ${usedPercent}%, #d8d7ff ${usedPercent}% 100%)`
          }}
        >
          <div className={styles.radialCenter}>
            <div>
              <div className={styles.radialValue}>{remaining}</div>
              <div className={styles.smallMuted}>days left</div>
            </div>
          </div>
        </div>
        <div className={styles.radialLegend}>
          {section.items.map((item) => (
            <div className={styles.radialLegendItem} key={item.itemId}>
              <div className={styles.smallMuted}>{item.label}</div>
              <div className={styles.agentName}>{item.metricValue}</div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (section.kind === 'people_cards') {
    return <CardGrid section={section} styles={styles} />;
  }

  if (section.kind === 'org_chart') {
    return (
      <div className={styles.orgChart}>
        {section.items.map((item, index) => (
          <React.Fragment key={item.itemId}>
            {index > 0 && <div className={styles.orgArrow}>&gt;</div>}
            <div className={styles.miniCard}>
              <div className={styles.avatar}>{initials(item.label)}</div>
              <div className={styles.agentName}>{item.label}</div>
              <div className={styles.smallMuted}>{item.value}</div>
            </div>
          </React.Fragment>
        ))}
      </div>
    );
  }

  if (section.kind === 'map') {
    const positions = [
      { left: '8%', top: '12%' },
      { left: '54%', top: '9%' },
      { left: '28%', top: '58%' },
      { left: '67%', top: '61%' },
      { left: '13%', top: '74%' },
      { left: '74%', top: '28%' }
    ];
    return (
      <div className={styles.map} role="img" aria-label={section.title}>
        {section.items.map((item, index) => (
          <div
            className={styles.mapPin}
            key={item.itemId}
            style={positions[index % positions.length]}
          >
            <div className={styles.agentName}>{item.label}</div>
            <div className={styles.smallMuted}>{item.description ?? item.value}</div>
          </div>
        ))}
      </div>
    );
  }

  if (section.kind === 'gallery') {
    return (
      <div className={styles.cardsGrid}>
        {section.items.map((item) => (
          <article className={styles.miniCard} key={item.itemId}>
            <div className={styles.galleryVisual}>{initials(item.label)}</div>
            <div className={styles.agentName}>{item.label}</div>
            {item.value && <Badge appearance="outline" color="informative">{item.value}</Badge>}
            {item.description && <div className={styles.smallMuted}>{item.description}</div>}
          </article>
        ))}
      </div>
    );
  }

  if (section.kind === 'document_callouts') {
    return (
      <div className={styles.sourceList}>
        {section.items.map((item) => (
          <article className={styles.callout} key={item.itemId}>
            <div className={styles.agentName}>{item.label}</div>
            <div className={styles.smallMuted}>{item.description}</div>
            <Button
              appearance="subtle"
              icon={<Open24Regular />}
              disabled={!item.href}
              onClick={() => openLink(item.href)}
            >
              Open source
            </Button>
          </article>
        ))}
      </div>
    );
  }

  if (section.kind === 'decision_tree') {
    return (
      <div className={styles.cardsGrid}>
        {section.items.map((item) => (
          <article className={styles.miniCard} key={item.itemId}>
            <Badge appearance="outline" color="informative">{categoryLabel(item.state ?? 'branch')}</Badge>
            <div className={styles.agentName}>{item.label}</div>
            <div className={styles.smallMuted}>{item.description}</div>
          </article>
        ))}
      </div>
    );
  }

  if (section.kind === 'approvals') {
    return (
      <div className={styles.sourceList}>
        {section.items.map((item) => (
          <article className={`${styles.callout} ${styles.warningCallout}`} key={item.itemId}>
            <Badge appearance="outline" color="warning">{categoryLabel(item.state ?? 'draft')}</Badge>
            <div className={styles.agentName}>{item.label}</div>
            <div className={styles.smallMuted}>{item.description}</div>
          </article>
        ))}
      </div>
    );
  }

  if (section.kind === 'policy_findings') {
    return (
      <div className={styles.sourceList}>
        {section.items.map((item) => (
          <article
            className={`${styles.callout} ${
              item.state === 'critical'
                ? styles.dangerCallout
                : item.state === 'resolved'
                  ? styles.successCallout
                  : styles.warningCallout
            }`}
            key={item.itemId}
          >
            <Badge
              appearance="filled"
              color={item.state === 'critical' ? 'danger' : item.state === 'resolved' ? 'success' : 'warning'}
            >
              {categoryLabel(item.state ?? 'warning')}
            </Badge>
            <div className={styles.agentName}>{item.label}</div>
            <div className={styles.smallMuted}>{item.description}</div>
            {item.value && <div className={styles.smallMuted}>{item.value}</div>}
          </article>
        ))}
      </div>
    );
  }

  if (section.kind === 'source_drawer') {
    return (
      <div className={styles.sourceList}>
        {section.items.map((item, index) => (
          <div className={styles.sourceRow} key={item.itemId}>
            <div className={styles.sourceNumber}>{index + 1}</div>
            <div>
              <div className={styles.agentName}>{item.label}</div>
              <div className={styles.smallMuted}>{item.description}</div>
            </div>
            <Button
              appearance="subtle"
              icon={<Open24Regular />}
              disabled={!item.href}
              onClick={() => openLink(item.href)}
            >
              Open
            </Button>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className={styles.actionButtons}>
      {section.items.map((item) => (
        <Button
          appearance="subtle"
          icon={<Chat24Regular />}
          key={item.itemId}
          onClick={() => sendFollowUp(item.label)}
        >
          {item.label}
        </Button>
      ))}
    </div>
  );
}

function CardGrid(
  props: {
    section: ScenarioSection;
    styles: ReturnType<typeof useStyles>;
  }
): React.ReactElement {
  return (
    <div className={props.styles.cardsGrid}>
      {props.section.items.map((item: ScenarioSectionItem) => (
        <article className={props.styles.miniCard} key={item.itemId}>
          <div className={props.styles.cardHeader}>
            <div className={props.styles.avatar}>{initials(item.label)}</div>
            <div>
              <div className={props.styles.agentName}>{item.label}</div>
              <div className={props.styles.smallMuted}>{item.value}</div>
            </div>
          </div>
          <div className={props.styles.smallMuted}>{item.description}</div>
        </article>
      ))}
    </div>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown host failure';
}
