import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { TariffsApi } from '../../core/api/tariffs.api';
import { ApiError } from '../../core/models/api-error.model';
import { TariffBand } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';
/** The rate card: every band the quotation desk may price against. */
@Component({
  selector: 'mrd-tariffs-home',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink,
    DataTableComponent,
    ErrorBannerComponent,
    PageHeaderComponent,
    PaginationComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header title="Tariffs" subtitle="Rate cards in force across the network">
        <a actions class="btn btn--sm btn--primary" routerLink="/tariffs/bands">Look up a band</a>
      </mrd-page-header>

      <mrd-error-banner
        [error]="failure()"
        heading="The rate card could not be read"
        (retry)="load()"
        (dismiss)="failure.set(null)"
      />

      <p class="explainer small">
        A quotation is priced against the first band whose mode matches the booking and whose
        weight range contains the chargeable weight. The rate per kilogram is applied to that
        weight; when the result falls below the minimum charge, the minimum charge is billed
        instead. Bands are read at the date of carriage, so a band that has expired stays on
        the card for consignments booked while it was in force.
      </p>

      <div class="card">
        <div class="card__header">
          <h2>Bands</h2>
          <span class="small muted">Sorted by mode, then by weight range</span>
        </div>
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          emptyTitle="No bands published"
          emptyMessage="No rate card is in force. The commercial desk publishes bands ahead of each tariff season."
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
  styles: [
    `
      .explainer {
        max-width: 70ch;
        margin: 0 0 16px;
        color: var(--mrd-ink-soft);
        line-height: 1.55;
      }
    `,
  ],
})
export class TariffsHomeComponent {
  private readonly api = inject(TariffsApi);

  readonly page = signal<Page<TariffBand>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly columns: TableColumn<TariffBand>[] = [
    { key: 'band', label: 'Band', mono: true, width: '150px' },
    { key: 'description', label: 'Description' },
    { key: 'mode', label: 'Mode', width: '90px' },
    {
      key: 'weight',
      label: 'Weight range',
      width: '170px',
      value: (row) => weightRange(row),
    },
    {
      key: 'ratePerKg',
      label: 'Rate per kg',
      align: 'end',
      width: '140px',
      value: (row) => `${row.ratePerKg.toFixed(2)} ${row.currency}`,
    },
    {
      key: 'minimumCharge',
      label: 'Minimum charge',
      align: 'end',
      width: '160px',
      value: (row) => `${row.minimumCharge.toFixed(2)} ${row.currency}`,
    },
    { key: 'validFrom', label: 'Valid from', width: '120px' },
    {
      key: 'validTo',
      label: 'Valid to',
      width: '120px',
      value: (row) => row.validTo ?? 'Open-ended',
    },
  ];

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.list().subscribe({
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
    // The whole card fits in one response today; the pager is here so the screen behaves
    // like every other list once the seasonal bands are added.
    this.page.update((current) => ({ ...current, page }));
    this.load();
  }
}

/** Weight ranges read as one column on the printed card, so they do here too. */
function weightRange(row: TariffBand): string {
  return `${row.minWeightKg.toLocaleString('en-GB')}–${row.maxWeightKg.toLocaleString('en-GB')} kg`;
}
