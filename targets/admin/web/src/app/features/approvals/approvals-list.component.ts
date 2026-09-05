import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';

import { toApiError } from '../../core/api/api-error.util';
import { ApprovalsApi } from '../../core/api/approvals.api';
import { ApiError } from '../../core/models/api-error.model';
import { Approval, ApprovalState } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FilterBarComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';

/** States the register can be filtered by; the empty value asks for everything. */
const STATES: ReadonlyArray<{ value: ApprovalState | ''; label: string }> = [
  { value: 'pending', label: 'Pending' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'withdrawn', label: 'Withdrawn' },
  { value: '', label: 'All states' },
];

const KINDS: Readonly<Record<Approval['kind'], string>> = {
  'credit-limit': 'Credit limit',
  'rate-override': 'Rate override',
  'write-off': 'Write-off',
  'account-opening': 'Account opening',
};

const STAMP = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

function stamp(value: string | undefined): string {
  if (!value) {
    return '';
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : STAMP.format(parsed);
}

/** The approvals register: what the commercial desk still has to decide. */
@Component({
  selector: 'mrd-approvals-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    DataTableComponent,
    ErrorBannerComponent,
    FilterBarComponent,
    PageHeaderComponent,
    PaginationComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Approvals"
        subtitle="Credit limits, rate overrides and write-offs raised by the branches"
      />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <mrd-filter-bar (reset)="clear()">
        <div class="field">
          <label for="approval-state">State</label>
          <select id="approval-state" [formControl]="state">
            @for (option of states; track option.label) {
              <option [value]="option.value">{{ option.label }}</option>
            }
          </select>
        </div>
      </mrd-filter-bar>

      <div class="card">
        <div class="card__header">
          <h2>Register</h2>
          <span class="muted small">{{ countLabel() }}</span>
        </div>
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          [link]="approvalLink"
          emptyTitle="Nothing in this state"
          emptyMessage="No request matches the state you picked. Choose “All states” to see the whole register."
        />
        <mrd-pagination
          [page]="page().page"
          [size]="page().size"
          [total]="page().total"
          (pageChange)="goToPage($event)"
        />
      </div>
    </div>
  `,
})
export class ApprovalsListComponent {
  private readonly api = inject(ApprovalsApi);

  readonly states = STATES;
  /** The desk lands on the work still waiting, which is what it opens the screen for. */
  readonly state = new FormControl<ApprovalState | ''>('pending', { nonNullable: true });
  /** Mirror of the control, so the header count re-reads when the filter moves. */
  readonly selectedState = signal<ApprovalState | ''>('pending');

  readonly page = signal<Page<Approval>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly countLabel = computed(() => {
    const total = this.page().total;
    const noun = total === 1 ? 'request' : 'requests';
    const state = this.selectedState();
    switch (state) {
      case 'pending':
        return `${total} ${noun} waiting`;
      case '':
        return `${total} ${noun} on the register`;
      default:
        return `${total} ${state} ${noun}`;
    }
  });

  readonly columns: TableColumn<Approval>[] = [
    { key: 'reference', label: 'Reference', mono: true, width: '120px' },
    { key: 'subject', label: 'Subject' },
    { key: 'kind', label: 'Kind', width: '150px', value: (row) => KINDS[row.kind] ?? row.kind },
    {
      key: 'amount',
      label: 'Amount',
      align: 'end',
      width: '140px',
      value: (row) =>
        row.amount === undefined
          ? ''
          : `${row.amount.toLocaleString('en-GB')} ${row.currency ?? ''}`.trim(),
    },
    { key: 'raisedBy', label: 'Raised by', width: '160px' },
    { key: 'raisedAt', label: 'Raised', width: '170px', value: (row) => stamp(row.raisedAt) },
    { key: 'state', label: 'State', pill: true, width: '110px' },
  ];

  constructor() {
    this.state.valueChanges.subscribe((value) => {
      // A different state is a different result set, so start again at the first page.
      this.selectedState.set(value);
      this.page.update((current) => ({ ...current, page: 1 }));
      this.load();
    });
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.list({ state: this.selectedState() || undefined, page: this.page().page }).subscribe({
      next: (result) => {
        this.page.set(result);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  goToPage(page: number): void {
    this.page.update((current) => ({ ...current, page }));
    this.load();
  }

  clear(): void {
    this.state.setValue('pending');
  }

  approvalLink = (row: Approval): unknown[] => ['/approvals', row.id];
}
