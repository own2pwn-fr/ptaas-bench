import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { interval } from 'rxjs';

import { toApiError } from '../../core/api/api-error.util';
import { ImportsApi } from '../../core/api/imports.api';
import { ApiError } from '../../core/models/api-error.model';
import { ImportJob } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';
import { formatArchiveSize } from './archive-upload.component';

/** How often the grid re-reads itself while a batch is still being unpacked. */
const POLL_INTERVAL_MS = 15_000;

const UPLOADED_AT = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

function formatUploadedAt(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : UPLOADED_AT.format(parsed);
}

/** States that mean the platform side is still working on the batch. */
const IN_PROGRESS: ReadonlySet<string> = new Set(['queued', 'extracting']);

/**
 * Every archive the platform team has loaded, newest first.
 *
 * Unpacking runs on the API side, so the only way to follow a batch is to re-read the
 * page. That is left off by default and only re-queries while something is actually
 * queued or extracting — an idle grid should not keep a query running all afternoon.
 */
@Component({
  selector: 'mrd-imports-history',
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
      <mrd-page-header
        title="Archive history"
        subtitle="Onboarding archives loaded by the platform team"
      >
        <button actions type="button" class="btn btn--sm" (click)="load()" [disabled]="loading()">
          Refresh
        </button>
        <a actions class="btn btn--sm btn--primary" routerLink="/imports">Load an archive</a>
      </mrd-page-header>

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <div class="card">
        <div class="card__header">
          <h2>Batches</h2>
          <label class="auto small">
            <input
              type="checkbox"
              [checked]="autoRefresh()"
              (change)="autoRefresh.set($any($event.target).checked)"
            />
            <span>Keep refreshing while archives are unpacking</span>
            @if (autoRefresh() && working()) {
              <span class="muted">· checking every 15 s</span>
            }
          </label>
        </div>

        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          emptyTitle="No archives loaded"
          emptyMessage="Nothing has been loaded in bulk yet. Onboarding packs appear here once uploaded."
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
      .auto {
        display: flex;
        align-items: center;
        gap: 6px;
        color: var(--mrd-ink-soft);
        white-space: nowrap;
      }

      .auto input {
        width: auto;
      }
    `,
  ],
})
export class ImportsHistoryComponent {
  private readonly api = inject(ImportsApi);

  readonly page = signal<Page<ImportJob>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);
  readonly autoRefresh = signal(false);

  /** True while at least one batch on this page is still queued or being unpacked. */
  readonly working = computed(() => this.page().items.some((job) => IN_PROGRESS.has(job.state)));

  readonly columns: TableColumn<ImportJob>[] = [
    {
      key: 'uploadedAt',
      label: 'Uploaded at',
      width: '170px',
      value: (row) => formatUploadedAt(row.uploadedAt),
    },
    { key: 'uploadedBy', label: 'Uploaded by', width: '160px' },
    { key: 'archive', label: 'Archive', mono: true },
    {
      key: 'sizeBytes',
      label: 'Size',
      align: 'end',
      width: '100px',
      value: (row) => formatArchiveSize(row.sizeBytes),
    },
    { key: 'entries', label: 'Entries', align: 'end', width: '90px' },
    { key: 'state', label: 'State', pill: true, width: '110px' },
    { key: 'message', label: 'Message' },
  ];

  constructor() {
    this.load();

    // One timer for the lifetime of the screen; it only issues a query when the operator
    // asked for it and there is still something in flight to watch.
    interval(POLL_INTERVAL_MS)
      .pipe(takeUntilDestroyed())
      .subscribe(() => {
        if (this.autoRefresh() && this.working() && !this.loading()) {
          this.load();
        }
      });
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
