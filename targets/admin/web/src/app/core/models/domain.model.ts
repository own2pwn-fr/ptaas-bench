/**
 * Business objects exchanged with the Meridian API.
 *
 * They stay flat on purpose: the API already denormalises the few joins the console
 * needs, and every list screen wants the display fields on the row itself.
 */
import { Role } from './session.model';

export type AccountStatus = 'active' | 'on-hold' | 'closed' | 'prospect';

export interface Account {
  id: string;
  /** Customer reference printed on paperwork, e.g. `CW-40118`. */
  reference: string;
  name: string;
  status: AccountStatus;
  country: string;
  incoterm: string;
  accountManager: string;
  openConsignments: number;
  outstandingBalance: number;
  currency: string;
  createdAt: string;
  updatedAt: string;
}

export interface AccountMember {
  id: string;
  displayName: string;
  role: Role;
  email?: string;
  phone?: string;
  jobTitle?: string;
  lastSeenAt?: string;
  status?: 'active' | 'invited' | 'suspended';
  [extra: string]: unknown;
}

export type InvoiceStatus = 'draft' | 'issued' | 'part-paid' | 'settled' | 'overdue';

export interface Invoice {
  id: string;
  number: string;
  accountId: string;
  accountName: string;
  status: InvoiceStatus;
  issuedOn: string;
  dueOn: string;
  net: number;
  vat: number;
  gross: number;
  currency: string;
}

export type ConsignmentStatus =
  | 'booked'
  | 'in-transit'
  | 'customs-hold'
  | 'delivered'
  | 'cancelled';

export interface Consignment {
  id: string;
  reference: string;
  accountId: string;
  accountName: string;
  status: ConsignmentStatus;
  origin: string;
  destination: string;
  mode: 'sea' | 'air' | 'road' | 'rail';
  weightKg: number;
  volumeCbm: number;
  etd: string;
  eta: string;
  vessel?: string;
}

export interface LedgerRow {
  id: string;
  postedOn: string;
  accountId: string;
  accountName: string;
  document: string;
  narrative: string;
  debit: number;
  credit: number;
  balance: number;
  currency: string;
}

export interface LedgerReport {
  window: string;
  account: string | null;
  openingBalance: number;
  closingBalance: number;
  currency: string;
  rows: LedgerRow[];
}

export interface SummaryTile {
  key: string;
  label: string;
  value: number;
  unit?: string;
  delta?: number;
}

export interface SummaryReport {
  accountId: string;
  accountName: string;
  generatedAt: string;
  tiles: SummaryTile[];
  topLanes: Array<{ lane: string; consignments: number; grossWeightKg: number }>;
}

export interface VolumePoint {
  bucket: string;
  consignments: number;
  teu: number;
  chargeableWeightKg: number;
}

export interface VolumeReport {
  from: string;
  to: string;
  granularity: 'day' | 'week' | 'month';
  points: VolumePoint[];
}

export interface Person {
  uid: string;
  displayName: string;
  surname: string;
  givenName: string;
  department: string;
  jobTitle: string;
  office: string;
  email: string;
  extension: string;
  managerUid?: string;
  managerName?: string;
  startedOn?: string;
}

export interface TariffBand {
  id: string;
  band: string;
  description: string;
  mode: 'sea' | 'air' | 'road' | 'rail';
  minWeightKg: number;
  maxWeightKg: number;
  ratePerKg: number;
  minimumCharge: number;
  currency: string;
  validFrom: string;
  validTo: string | null;
}

export interface IntakeDocument {
  id: string;
  receivedAt: string;
  channel: 'upload' | 'edi' | 'email' | 'api';
  documentType: string;
  reference: string;
  submittedBy: string;
  state: 'accepted' | 'rejected' | 'processing';
  lineCount: number;
  note?: string;
}

export interface IntakeReceipt {
  id: string;
  state: 'accepted' | 'rejected' | 'processing';
  documentType: string;
  lineCount: number;
  warnings: string[];
  receivedAt: string;
}

export interface ExportTemplate {
  id: string;
  name: string;
  /** Stored stylesheet name the render endpoint resolves. */
  stylesheet: string;
  format: 'pdf' | 'csv' | 'xlsx' | 'xml';
  updatedAt: string;
  updatedBy: string;
}

export interface ExportJob {
  id: string;
  requestedAt: string;
  requestedBy: string;
  format: string;
  rows: number;
  state: 'queued' | 'running' | 'done' | 'failed';
  artefact?: string;
  durationMs?: number;
}

export interface RenderResult {
  jobId: string;
  contentType: string;
  bytes: number;
  preview: string;
}

export interface RuleSummary {
  id: string;
  name: string;
  scope: 'consignment' | 'invoice' | 'account' | 'document';
  expression: string;
  enabled: boolean;
  updatedAt: string;
  updatedBy: string;
  lastMatchedAt?: string;
  matchCount?: number;
}

export interface RulePreviewResult {
  matched: boolean;
  value: string;
  elapsedMs: number;
  notes: string[];
}

export type ApprovalState = 'pending' | 'approved' | 'rejected' | 'withdrawn';

export interface Approval {
  id: string;
  reference: string;
  subject: string;
  kind: 'credit-limit' | 'rate-override' | 'write-off' | 'account-opening';
  state: ApprovalState;
  amount?: number;
  currency?: string;
  raisedBy: string;
  raisedAt: string;
  decidedBy?: string;
  decidedAt?: string;
  note?: string;
  accountId?: string;
  accountName?: string;
}

export type NoticeSeverity = 'info' | 'warning' | 'critical';

export interface Notice {
  id: string;
  title: string;
  /** Notice body, authored in the console; may contain live counters. */
  body: string;
  severity: NoticeSeverity;
  publishedFrom: string;
  publishedTo: string | null;
  author: string;
  active?: boolean;
}

export interface NotificationTemplate {
  id: string;
  name: string;
  channel: 'email' | 'sms' | 'webhook';
  subject: string;
  body: string;
  updatedAt: string;
  updatedBy: string;
}

export interface NotificationPreview {
  subject: string;
  body: string;
  channel: string;
  renderedAt: string;
}

export interface NotificationLogEntry {
  id: string;
  sentAt: string;
  channel: string;
  template: string;
  recipient: string;
  state: 'sent' | 'bounced' | 'deferred' | 'suppressed';
  detail?: string;
}

export interface AuditEvent {
  id: string;
  at: string;
  actor: string;
  actorId: string;
  action: string;
  target: string;
  outcome: 'success' | 'denied' | 'error';
  sourceAddress: string;
  userAgent?: string;
  /** Present when the detail drawer asked the API to expand the actor. */
  actorDetail?: Person | null;
  attributes?: Record<string, unknown>;
}

export interface ImportJob {
  id: string;
  uploadedAt: string;
  uploadedBy: string;
  archive: string;
  sizeBytes: number;
  entries: number;
  state: 'queued' | 'extracting' | 'done' | 'failed';
  message?: string;
}

export interface Integration {
  id: string;
  name: string;
  kind: 'edi' | 'webhook' | 'sftp' | 'erp';
  endpoint: string;
  enabled: boolean;
  lastDeliveryAt?: string;
  lastDeliveryState?: 'ok' | 'failed' | 'never';
  owner: string;
}

export interface ProbeResult {
  endpoint: string;
  status: number;
  elapsedMs: number;
  contentType: string;
  body: string;
}

export interface WorkspaceLayout {
  /** Base64 encoding of the saved grid arrangement. */
  state: string;
  updatedAt?: string;
  updatedBy?: string;
}

export interface Profile {
  subjectId: string;
  displayName: string;
  email: string;
  phone: string;
  jobTitle: string;
  office: string;
  locale: string;
  timeZone: string;
  digestOptIn: boolean;
}

export interface SearchHit {
  id: string;
  kind: 'account' | 'consignment' | 'invoice' | 'person' | 'notice';
  title: string;
  reference: string;
  summary: string;
  updatedAt: string;
  routerLink: string[];
}
