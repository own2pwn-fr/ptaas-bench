import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { debounceTime } from 'rxjs';

import { AccountsApi } from '../../core/api/accounts.api';
import { toApiError } from '../../core/api/api-error.util';
import { ApiError } from '../../core/models/api-error.model';
import { Account } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FilterBarComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';

/** The account book: every customer the group forwards for. */
@Component({
  selector: 'mrd-account-list',
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
      <mrd-page-header title="Accounts" subtitle="Customers, their balances and open work" />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <mrd-filter-bar (reset)="clear()">
        <div class="field">
          <label for="account-q">Search</label>
          <input
            id="account-q"
            type="search"
            [formControl]="term"
            placeholder="Name, reference or account manager"
          />
        </div>
      </mrd-filter-bar>

      <div class="card">
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          [link]="accountLink"
          emptyTitle="No accounts match"
          emptyMessage="Try a shorter search term, or clear the filters."
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
export class AccountListComponent {
  private readonly api = inject(AccountsApi);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  readonly term = new FormControl('', { nonNullable: true });
  readonly page = signal<Page<Account>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly columns: TableColumn<Account>[] = [
    { key: 'reference', label: 'Reference', mono: true, width: '110px' },
    { key: 'name', label: 'Account' },
    { key: 'country', label: 'Country', width: '110px' },
    { key: 'accountManager', label: 'Account manager', width: '180px' },
    { key: 'incoterm', label: 'Incoterm', width: '100px' },
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
    this.route.queryParamMap.subscribe((params) => {
      this.term.setValue(params.get('q') ?? '', { emitEvent: false });
      this.load();
    });

    this.term.valueChanges.pipe(debounceTime(300)).subscribe((value) => {
      void this.router.navigate(['/orgs'], {
        queryParams: { q: value || null, page: 1 },
        queryParamsHandling: 'merge',
      });
    });
  }

  load(): void {
    const params = this.route.snapshot.queryParamMap;
    const page = Number.parseInt(params.get('page') ?? '1', 10);

    this.loading.set(true);
    this.failure.set(null);
    this.api.list({ page: page > 0 ? page : 1, size: 25, q: params.get('q') ?? '' }).subscribe({
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
    void this.router.navigate(['/orgs'], {
      queryParams: { page },
      queryParamsHandling: 'merge',
    });
  }

  clear(): void {
    this.term.setValue('');
    void this.router.navigate(['/orgs']);
  }

  accountLink = (row: Account): unknown[] => ['/orgs', row.id];
}
