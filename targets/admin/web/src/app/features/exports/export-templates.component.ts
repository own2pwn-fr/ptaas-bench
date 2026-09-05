import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { ExportsApi } from '../../core/api/exports.api';
import { ApiError } from '../../core/models/api-error.model';
import { ExportTemplate } from '../../core/models/domain.model';
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
 * The layouts the render screen offers.
 *
 * Read-only here on purpose: the layouts themselves are versioned alongside the rest of
 * the customer paperwork and are changed by the documentation team, not from the console.
 */
@Component({
  selector: 'mrd-export-templates',
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
        title="Stored layouts"
        subtitle="What a rendered statement looks like once it reaches the customer"
      >
        <button actions type="button" class="btn btn--sm" (click)="load()">Refresh</button>
        <a actions class="btn btn--sm btn--primary" routerLink="/exports">Render a statement</a>
      </mrd-page-header>

      <mrd-exports-tabs />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <p class="muted small note">
        Layouts are maintained by the documentation team: they hold the wording, the column
        order and the customer's own headed paper. Ask them on extension 4188 for a change, or
        paste a one-off layout on the render screen when a customer sends their own.
      </p>

      <div class="card">
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          emptyTitle="No stored layouts"
          emptyMessage="Nothing has been published for this account yet; statements will have to use a supplied layout."
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
      .note {
        max-width: 70ch;
        margin: 0 0 16px;
      }
    `,
  ],
})
export class ExportTemplatesComponent {
  private readonly api = inject(ExportsApi);

  readonly page = signal<Page<ExportTemplate>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly columns: TableColumn<ExportTemplate>[] = [
    { key: 'name', label: 'Layout' },
    { key: 'stylesheet', label: 'Stored name', mono: true, width: '220px' },
    { key: 'format', label: 'Format', width: '90px' },
    {
      key: 'updatedAt',
      label: 'Updated',
      width: '170px',
      value: (row) => timestamp(row.updatedAt),
    },
    { key: 'updatedBy', label: 'Updated by', width: '160px' },
  ];

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.templates().subscribe({
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
    // The layout library fits on one page today; the pager is here so the screen behaves
    // like every other list once each customer has their own variant.
    this.page.update((current) => ({ ...current, page }));
    this.load();
  }
}

/** Local time to the minute, matching the other lists in the console. */
function timestamp(value: string): string {
  return new Date(value).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}
