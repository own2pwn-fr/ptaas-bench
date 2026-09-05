import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { IntakeApi } from '../../core/api/intake.api';
import { ApiError } from '../../core/models/api-error.model';
import { IntakeDocument } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';
import { IntakeTabsComponent } from './intake-tabs.component';

/**
 * Everything the intake service has taken in, newest first.
 *
 * The desk works this list backwards when a partner claims to have sent something: the
 * channel column is usually enough to tell a failed feed from a manual re-send.
 */
@Component({
  selector: 'mrd-intake-history',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink,
    DataTableComponent,
    ErrorBannerComponent,
    IntakeTabsComponent,
    PageHeaderComponent,
    PaginationComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Intake history"
        subtitle="Carrier messages and manifests the console has taken in"
      >
        <button actions type="button" class="btn btn--sm" (click)="load()">Refresh</button>
        <a actions class="btn btn--sm btn--primary" routerLink="/intake">Submit a document</a>
      </mrd-page-header>

      <mrd-intake-tabs />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <div class="card">
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          emptyTitle="Nothing taken in yet"
          emptyMessage="No carrier message or manifest has reached the intake service on this account."
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
export class IntakeHistoryComponent {
  private readonly api = inject(IntakeApi);

  readonly page = signal<Page<IntakeDocument>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly columns: TableColumn<IntakeDocument>[] = [
    {
      key: 'receivedAt',
      label: 'Received',
      width: '170px',
      value: (row) => timestamp(row.receivedAt),
    },
    { key: 'channel', label: 'Channel', width: '100px' },
    { key: 'documentType', label: 'Document type', width: '170px' },
    { key: 'reference', label: 'Reference', mono: true, width: '130px' },
    { key: 'submittedBy', label: 'Submitted by', width: '160px' },
    { key: 'state', label: 'State', pill: true, width: '110px' },
    { key: 'lineCount', label: 'Lines', align: 'end', width: '80px' },
    { key: 'note', label: 'Note' },
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

/** Local time to the minute; the desk compares these against carrier emails all day. */
function timestamp(value: string): string {
  return new Date(value).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}
