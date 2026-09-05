import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { ExportsApi } from '../../core/api/exports.api';
import { ApiError } from '../../core/models/api-error.model';
import { ExportJob } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';
import { ExportsTabsComponent } from './exports-tabs.component';

/**
 * Every render and extract asked for, newest first.
 *
 * The finance desk lives on this screen at quarter end: the duration column is what tells
 * them whether a run is still worth waiting for or should be asked for again overnight.
 */
@Component({
  selector: 'mrd-export-history',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink,
    DataTableComponent,
    ErrorBannerComponent,
    ExportsTabsComponent,
    PageHeaderComponent,
    PaginationComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Export history"
        subtitle="Rendered statements and bulk extracts, with the artefact each run produced"
      >
        <button actions type="button" class="btn btn--sm" (click)="load()">Refresh</button>
        <a actions class="btn btn--sm btn--primary" routerLink="/exports">New extract</a>
      </mrd-page-header>

      <mrd-exports-tabs />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <div class="card">
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          emptyTitle="No exports yet"
          emptyMessage="Nothing has been rendered or extracted on this account. Start from the render screen."
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
export class ExportHistoryComponent {
  private readonly api = inject(ExportsApi);

  readonly page = signal<Page<ExportJob>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly columns: TableColumn<ExportJob>[] = [
    {
      key: 'requestedAt',
      label: 'Requested',
      width: '170px',
      value: (row) => timestamp(row.requestedAt),
    },
    { key: 'requestedBy', label: 'Requested by', width: '170px' },
    { key: 'format', label: 'Format', width: '90px' },
    { key: 'rows', label: 'Rows', align: 'end', width: '100px' },
    { key: 'state', label: 'State', pill: true, width: '110px' },
    {
      key: 'durationMs',
      label: 'Duration',
      align: 'end',
      width: '110px',
      value: (row) => (row.durationMs === undefined ? '' : `${row.durationMs} ms`),
    },
    { key: 'artefact', label: 'Artefact', mono: true },
  ];

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.history(this.page().page).subscribe({
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
}

/** Local time to the minute; runs are compared against the finance desk's own timings. */
function timestamp(value: string): string {
  return new Date(value).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}
