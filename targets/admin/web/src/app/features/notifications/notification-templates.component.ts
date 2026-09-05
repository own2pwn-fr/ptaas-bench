import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { NotificationsApi } from '../../core/api/notifications.api';
import { ApiError } from '../../core/models/api-error.model';
import { NotificationTemplate } from '../../core/models/domain.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  PaginationComponent,
  StatusPillComponent,
  TableColumn,
} from '../../shared';
import { NotificationsTabsComponent } from './notifications-tabs.component';

/** Rows per page. The library is small enough that the whole set arrives at once. */
const PAGE_SIZE = 25;

/**
 * The stored message library.
 *
 * The endpoint answers with the complete set in one envelope — there are a few dozen
 * templates, not thousands — so the pager works over what came back rather than asking
 * the API again for each page.
 */
@Component({
  selector: 'mrd-notification-templates',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    RouterLink,
    DataTableComponent,
    ErrorBannerComponent,
    NotificationsTabsComponent,
    PageHeaderComponent,
    PaginationComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Notification templates"
        subtitle="Wording used for arrival notices, customs holds and invoice reminders"
      >
        <a actions class="btn btn--sm" routerLink="/notifications">Preview</a>
      </mrd-page-header>

      <mrd-notifications-tabs />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <div class="card">
        <mrd-data-table
          [columns]="columns"
          [rows]="visible()"
          [loading]="loading()"
          (rowSelect)="select($event)"
          emptyTitle="No templates"
          emptyMessage="Nothing has been stored yet. Draft the wording on the preview screen first."
        />
        <mrd-pagination
          [page]="page()"
          [size]="size"
          [total]="templates().length"
          (pageChange)="goToPage($event)"
        />
      </div>

      @if (selected(); as template) {
        <section class="card template-detail">
          <div class="card__header">
            <h2>{{ template.name }}</h2>
            <button type="button" class="btn btn--sm btn--ghost" (click)="selected.set(null)">
              Close
            </button>
          </div>
          <div class="card__body">
            <dl class="detail">
              <dt>Channel</dt>
              <dd><mrd-status-pill [status]="template.channel" /></dd>
              <dt>Subject</dt>
              <dd>{{ template.subject }}</dd>
              <dt>Last change</dt>
              <dd>{{ template.updatedAt | date: 'd MMM y, HH:mm' }} by {{ template.updatedBy }}</dd>
            </dl>
            <h3 class="template-detail__heading">Body</h3>
            <pre class="payload">{{ template.body }}</pre>
          </div>
        </section>
      } @else if (!loading() && templates().length > 0) {
        <p class="muted small">Select a row to read the body of a template.</p>
      }
    </div>
  `,
  styles: [
    `
      .template-detail {
        margin-top: 16px;
      }

      .template-detail__heading {
        margin: 16px 0 6px;
      }
    `,
  ],
})
export class NotificationTemplatesComponent {
  private readonly api = inject(NotificationsApi);

  readonly size = PAGE_SIZE;
  readonly templates = signal<NotificationTemplate[]>([]);
  readonly page = signal(1);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);
  readonly selected = signal<NotificationTemplate | null>(null);

  readonly visible = computed(() => {
    const start = (this.page() - 1) * PAGE_SIZE;
    return this.templates().slice(start, start + PAGE_SIZE);
  });

  readonly columns: TableColumn<NotificationTemplate>[] = [
    { key: 'name', label: 'Template' },
    { key: 'channel', label: 'Channel', pill: true, width: '110px' },
    { key: 'subject', label: 'Subject' },
    {
      key: 'updatedAt',
      label: 'Updated',
      width: '170px',
      value: (row) => formatStamp(row.updatedAt),
    },
    { key: 'updatedBy', label: 'Updated by', width: '150px' },
  ];

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.selected.set(null);
    this.api.templates().subscribe({
      next: (page) => {
        this.templates.set(page.items);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  goToPage(page: number): void {
    this.page.set(page);
    this.selected.set(null);
  }

  select(row: unknown): void {
    this.selected.set(row as NotificationTemplate);
  }
}

/** Table cells format their own dates; the pipes are used in the detail panel. */
function formatStamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}
