import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AccountsApi } from '../../core/api/accounts.api';
import { toApiError } from '../../core/api/api-error.util';
import { ApprovalsApi } from '../../core/api/approvals.api';
import { ReportsApi } from '../../core/api/reports.api';
import { ApiError } from '../../core/models/api-error.model';
import { Account, Approval, SummaryReport } from '../../core/models/domain.model';
import { SessionService } from '../../core/services/session.service';
import { SupportDeskService } from '../../core/services/support-desk.service';
import {
  DataTableComponent,
  EmptyStateComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
  TableColumn,
} from '../../shared';

/** Overview: what the operator on shift needs before opening anything else. */
@Component({
  selector: 'mrd-dashboard',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    DataTableComponent,
    EmptyStateComponent,
    ErrorBannerComponent,
    PageHeaderComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent {
  private readonly reports = inject(ReportsApi);
  private readonly approvals = inject(ApprovalsApi);
  private readonly accountsApi = inject(AccountsApi);

  readonly session = inject(SessionService);
  readonly desk = inject(SupportDeskService);

  readonly summary = signal<SummaryReport | null>(null);
  readonly summaryLoading = signal(true);
  readonly pending = signal<Approval[]>([]);
  readonly pendingLoading = signal(true);
  readonly accounts = signal<Account[]>([]);
  readonly accountsLoading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly greeting = computed(() => {
    const hour = new Date().getHours();
    const name = (this.session.displayName() ?? '').split(' ').slice(-1)[0] || 'there';
    if (hour < 12) {
      return `Good morning, ${name}`;
    }
    return hour < 18 ? `Good afternoon, ${name}` : `Good evening, ${name}`;
  });

  readonly canSeeApprovals = computed(() => this.session.hasRole('analyst'));

  readonly approvalColumns: TableColumn<Approval>[] = [
    { key: 'reference', label: 'Reference', mono: true, width: '120px' },
    { key: 'subject', label: 'Subject' },
    { key: 'kind', label: 'Kind', width: '140px' },
    {
      key: 'amount',
      label: 'Amount',
      align: 'end',
      width: '130px',
      value: (row) =>
        row.amount === undefined ? '' : `${row.amount.toLocaleString('en-GB')} ${row.currency ?? ''}`,
    },
    { key: 'raisedBy', label: 'Raised by', width: '160px' },
  ];

  readonly accountColumns: TableColumn<Account>[] = [
    { key: 'reference', label: 'Reference', mono: true, width: '110px' },
    { key: 'name', label: 'Account' },
    { key: 'status', label: 'Status', pill: true, width: '110px' },
    { key: 'openConsignments', label: 'Open', align: 'end', width: '80px' },
    {
      key: 'outstandingBalance',
      label: 'Outstanding',
      align: 'end',
      width: '140px',
      value: (row) => `${row.outstandingBalance.toLocaleString('en-GB')} ${row.currency}`,
    },
  ];

  constructor() {
    this.load();
  }

  load(): void {
    this.failure.set(null);

    this.summaryLoading.set(true);
    this.reports.summary().subscribe({
      next: (summary) => {
        this.summary.set(summary);
        this.summaryLoading.set(false);
      },
      error: (error: unknown) => {
        this.summaryLoading.set(false);
        this.failure.set(toApiError(error));
      },
    });

    this.accountsLoading.set(true);
    this.accountsApi.list({ page: 1, size: 6 }).subscribe({
      next: (page) => {
        this.accounts.set(page.items);
        this.accountsLoading.set(false);
      },
      error: () => this.accountsLoading.set(false),
    });

    if (!this.canSeeApprovals()) {
      this.pendingLoading.set(false);
      return;
    }

    this.pendingLoading.set(true);
    this.approvals.list({ state: 'pending', page: 1 }).subscribe({
      next: (page) => {
        this.pending.set(page.items.slice(0, 6));
        this.pendingLoading.set(false);
      },
      error: () => this.pendingLoading.set(false),
    });
  }

  approvalLink = (row: Approval): unknown[] => ['/approvals', row.id];
  accountLink = (row: Account): unknown[] => ['/orgs', row.id];
}
