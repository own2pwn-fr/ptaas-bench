import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AccountsApi } from '../../core/api/accounts.api';
import { toApiError } from '../../core/api/api-error.util';
import { ApiError } from '../../core/models/api-error.model';
import { Invoice } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FilterBarComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';
import { AccountTabsComponent } from './account-tabs.component';

const STATUSES = ['draft', 'issued', 'part-paid', 'settled', 'overdue'];

/** Everything invoiced on one account. */
@Component({
  selector: 'mrd-account-invoices',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    AccountTabsComponent,
    DataTableComponent,
    ErrorBannerComponent,
    FilterBarComponent,
    PageHeaderComponent,
    PaginationComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header title="Invoices" subtitle="Issued against this account">
        <a actions class="btn btn--sm" [routerLink]="['/orgs', orgId()]">Back to account</a>
      </mrd-page-header>

      <mrd-account-tabs [orgId]="orgId()" />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <mrd-filter-bar (reset)="setStatus('')">
        <div class="field">
          <label for="invoice-status">Status</label>
          <select id="invoice-status" [value]="status()" (change)="setStatus($any($event.target).value)">
            <option value="">All statuses</option>
            @for (option of statuses; track option) {
              <option [value]="option">{{ option }}</option>
            }
          </select>
        </div>
      </mrd-filter-bar>

      <div class="card">
        <div class="card__header">
          <h2>{{ page().total }} invoices</h2>
          <span class="small muted">
            Outstanding {{ outstanding() | number: '1.2-2' }} · updated
            {{ refreshedAt() | date: 'HH:mm' }}
          </span>
        </div>
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          emptyTitle="No invoices"
          emptyMessage="Nothing has been invoiced on this account with that status."
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
export class AccountInvoicesComponent {
  readonly orgId = input.required<string>();

  private readonly api = inject(AccountsApi);

  readonly statuses = STATUSES;
  readonly status = signal('');
  readonly page = signal<Page<Invoice>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);
  readonly refreshedAt = signal(new Date());

  readonly columns: TableColumn<Invoice>[] = [
    { key: 'number', label: 'Invoice', mono: true, width: '140px' },
    { key: 'status', label: 'Status', pill: true, width: '110px' },
    { key: 'issuedOn', label: 'Issued', width: '110px' },
    { key: 'dueOn', label: 'Due', width: '110px' },
    { key: 'net', label: 'Net', align: 'end', value: (row) => row.net.toLocaleString('en-GB') },
    { key: 'vat', label: 'VAT', align: 'end', value: (row) => row.vat.toLocaleString('en-GB') },
    {
      key: 'gross',
      label: 'Gross',
      align: 'end',
      value: (row) => `${row.gross.toLocaleString('en-GB')} ${row.currency}`,
    },
  ];

  constructor() {
    effect(() => {
      if (this.orgId()) {
        this.load();
      }
    });
  }

  outstanding(): number {
    return this.page()
      .items.filter((invoice) => invoice.status === 'overdue' || invoice.status === 'issued')
      .reduce((total, invoice) => total + invoice.gross, 0);
  }

  setStatus(status: string): void {
    this.status.set(status);
    this.page.update((current) => ({ ...current, page: 1 }));
    this.load();
  }

  goToPage(page: number): void {
    this.page.update((current) => ({ ...current, page }));
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.invoices(this.orgId(), { status: this.status(), page: this.page().page }).subscribe({
      next: (result) => {
        this.page.set(result);
        this.refreshedAt.set(new Date());
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }
}
