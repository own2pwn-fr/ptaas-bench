import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AccountsApi } from '../../core/api/accounts.api';
import { toApiError } from '../../core/api/api-error.util';
import { ApiError } from '../../core/models/api-error.model';
import { Consignment } from '../../core/models/domain.model';
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

const STATUSES = ['booked', 'in-transit', 'customs-hold', 'delivered', 'cancelled'];

/** Freight moving for one account. */
@Component({
  selector: 'mrd-account-consignments',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
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
      <mrd-page-header title="Consignments" subtitle="Booked, moving and delivered">
        <a actions class="btn btn--sm" [routerLink]="['/orgs', orgId()]">Back to account</a>
      </mrd-page-header>

      <mrd-account-tabs [orgId]="orgId()" />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <mrd-filter-bar (reset)="setStatus('')">
        <div class="field">
          <label for="consignment-status">Status</label>
          <select
            id="consignment-status"
            [value]="status()"
            (change)="setStatus($any($event.target).value)"
          >
            <option value="">All statuses</option>
            @for (option of statuses; track option) {
              <option [value]="option">{{ option }}</option>
            }
          </select>
        </div>
      </mrd-filter-bar>

      <div class="card">
        <div class="card__header">
          <h2>{{ page().total }} consignments</h2>
          <span class="small muted">
            {{ totalWeight() | number: '1.0-0' }} kg across this page
          </span>
        </div>
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          emptyTitle="Nothing booked"
          emptyMessage="No consignment on this account matches that status."
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
export class AccountConsignmentsComponent {
  readonly orgId = input.required<string>();

  private readonly api = inject(AccountsApi);

  readonly statuses = STATUSES;
  readonly status = signal('');
  readonly page = signal<Page<Consignment>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly columns: TableColumn<Consignment>[] = [
    { key: 'reference', label: 'Reference', mono: true, width: '120px' },
    { key: 'origin', label: 'Origin' },
    { key: 'destination', label: 'Destination' },
    { key: 'mode', label: 'Mode', width: '80px' },
    { key: 'vessel', label: 'Vessel / flight', width: '160px' },
    { key: 'status', label: 'Status', pill: true, width: '130px' },
    {
      key: 'weightKg',
      label: 'Weight',
      align: 'end',
      width: '110px',
      value: (row) => `${row.weightKg.toLocaleString('en-GB')} kg`,
    },
    { key: 'etd', label: 'ETD', width: '110px' },
    { key: 'eta', label: 'ETA', width: '110px' },
  ];

  constructor() {
    effect(() => {
      if (this.orgId()) {
        this.load();
      }
    });
  }

  totalWeight(): number {
    return this.page().items.reduce((total, row) => total + row.weightKg, 0);
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
    this.api
      .consignments(this.orgId(), { status: this.status(), page: this.page().page })
      .subscribe({
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
}
