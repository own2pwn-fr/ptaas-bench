import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AccountsApi } from '../../core/api/accounts.api';
import { toApiError } from '../../core/api/api-error.util';
import { ApiError } from '../../core/models/api-error.model';
import { Account, Consignment, Invoice } from '../../core/models/domain.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
  TableColumn,
} from '../../shared';
import { AccountTabsComponent } from './account-tabs.component';

/** One customer account: the commercial facts, plus what is currently in flight. */
@Component({
  selector: 'mrd-account-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    AccountTabsComponent,
    DataTableComponent,
    ErrorBannerComponent,
    PageHeaderComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        [title]="account()?.name ?? 'Account'"
        [subtitle]="account() ? account()!.reference + ' · ' + account()!.country : 'Loading…'"
      >
        <a actions class="btn btn--sm" [routerLink]="['/orgs', orgId(), 'invoices']">Invoices</a>
        <a actions class="btn btn--sm btn--primary" [routerLink]="['/orgs', orgId(), 'consignments']">
          Consignments
        </a>
      </mrd-page-header>

      <mrd-account-tabs [orgId]="orgId()" />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      @if (loading()) {
        <div class="card" style="padding: 16px">
          <mrd-skeleton [rows]="6" />
        </div>
      } @else if (account()) {
        @let record = account()!;
        <div class="grid grid--2">
          <section class="card">
            <div class="card__header"><h2>Commercial</h2></div>
            <div class="card__body">
              <dl class="detail">
                <dt>Status</dt>
                <dd><mrd-status-pill [status]="record.status" /></dd>
                <dt>Account manager</dt>
                <dd>{{ record.accountManager }}</dd>
                <dt>Standard incoterm</dt>
                <dd>{{ record.incoterm }}</dd>
                <dt>Outstanding balance</dt>
                <dd>{{ record.outstandingBalance | number: '1.2-2' }} {{ record.currency }}</dd>
                <dt>Open consignments</dt>
                <dd>{{ record.openConsignments }}</dd>
                <dt>Customer since</dt>
                <dd>{{ record.createdAt | date: 'd MMM y' }}</dd>
                <dt>Last change</dt>
                <dd>{{ record.updatedAt | date: 'd MMM y, HH:mm' }}</dd>
              </dl>
            </div>
          </section>

          <section class="card">
            <div class="card__header">
              <h2>Latest invoices</h2>
              <a class="small" [routerLink]="['/orgs', orgId(), 'invoices']">All invoices</a>
            </div>
            <mrd-data-table
              [columns]="invoiceColumns"
              [rows]="invoices()"
              [loading]="invoicesLoading()"
              emptyTitle="Nothing invoiced yet"
              emptyMessage="No invoice has been issued on this account."
            />
          </section>
        </div>

        <section class="card" style="margin-top: 16px">
          <div class="card__header">
            <h2>Consignments in flight</h2>
            <a class="small" [routerLink]="['/orgs', orgId(), 'consignments']">All consignments</a>
          </div>
          <mrd-data-table
            [columns]="consignmentColumns"
            [rows]="consignments()"
            [loading]="consignmentsLoading()"
            emptyTitle="Nothing moving"
            emptyMessage="This account has no consignment currently in transit."
          />
        </section>
      }
    </div>
  `,
})
export class AccountDetailComponent {
  /** Bound from the route path by the router's component input binding. */
  readonly orgId = input.required<string>();

  private readonly api = inject(AccountsApi);

  readonly account = signal<Account | null>(null);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);
  readonly invoices = signal<Invoice[]>([]);
  readonly invoicesLoading = signal(true);
  readonly consignments = signal<Consignment[]>([]);
  readonly consignmentsLoading = signal(true);

  readonly invoiceColumns: TableColumn<Invoice>[] = [
    { key: 'number', label: 'Invoice', mono: true, width: '130px' },
    { key: 'status', label: 'Status', pill: true, width: '110px' },
    { key: 'dueOn', label: 'Due', width: '110px' },
    {
      key: 'gross',
      label: 'Gross',
      align: 'end',
      value: (row) => `${row.gross.toLocaleString('en-GB')} ${row.currency}`,
    },
  ];

  readonly consignmentColumns: TableColumn<Consignment>[] = [
    { key: 'reference', label: 'Reference', mono: true, width: '120px' },
    { key: 'origin', label: 'Origin' },
    { key: 'destination', label: 'Destination' },
    { key: 'mode', label: 'Mode', width: '80px' },
    { key: 'status', label: 'Status', pill: true, width: '130px' },
    { key: 'eta', label: 'ETA', width: '110px' },
  ];

  constructor() {
    effect(() => {
      const id = this.orgId();
      if (id) {
        this.load();
      }
    });
  }

  load(): void {
    const id = this.orgId();

    this.loading.set(true);
    this.failure.set(null);
    this.api.get(id).subscribe({
      next: (account) => {
        this.account.set(account);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });

    this.invoicesLoading.set(true);
    this.api.invoices(id, { page: 1 }).subscribe({
      next: (page) => {
        this.invoices.set(page.items.slice(0, 5));
        this.invoicesLoading.set(false);
      },
      error: () => this.invoicesLoading.set(false),
    });

    this.consignmentsLoading.set(true);
    this.api.consignments(id, { status: 'in-transit', page: 1 }).subscribe({
      next: (page) => {
        this.consignments.set(page.items.slice(0, 6));
        this.consignmentsLoading.set(false);
      },
      error: () => this.consignmentsLoading.set(false),
    });
  }
}
