import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { NotificationsApi } from '../../core/api/notifications.api';
import { ApiError } from '../../core/models/api-error.model';
import { NotificationLogEntry } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FilterBarComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';
import { NotificationsTabsComponent } from './notifications-tabs.component';

/** Delivery states the API reports, in the order the filter lists them. */
const STATES: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'sent', label: 'Sent' },
  { value: 'deferred', label: 'Deferred' },
  { value: 'bounced', label: 'Bounced' },
  { value: 'suppressed', label: 'Suppressed' },
];

/**
 * What actually went out.
 *
 * The log endpoint pages on the server and takes no state parameter, so the filter
 * narrows the page that is already loaded. That is what the desk wants anyway: they open
 * the log to see whether the last batch of arrival notices reached the customer.
 */
@Component({
  selector: 'mrd-notification-log',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink,
    DataTableComponent,
    ErrorBannerComponent,
    FilterBarComponent,
    NotificationsTabsComponent,
    PageHeaderComponent,
    PaginationComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Delivery log"
        subtitle="Messages sent to customers, EDI partners and staff"
      >
        <a actions class="btn btn--sm" routerLink="/notifications">Preview</a>
      </mrd-page-header>

      <mrd-notifications-tabs />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <mrd-filter-bar (reset)="clear()">
        <div class="field">
          <label for="log-state">Delivery state</label>
          <select id="log-state" [value]="state()" (change)="state.set($any($event.target).value)">
            <option value="">Every state</option>
            @for (option of states; track option.value) {
              <option [value]="option.value">{{ option.label }}</option>
            }
          </select>
          <span class="field__hint">{{ filterHint() }}</span>
        </div>

        <button
          extraActions
          type="button"
          class="btn btn--sm btn--primary"
          [disabled]="loading()"
          (click)="load()"
        >
          {{ loading() ? 'Refreshing…' : 'Refresh' }}
        </button>
      </mrd-filter-bar>

      <div class="card">
        <mrd-data-table
          [columns]="columns"
          [rows]="rows()"
          [loading]="loading()"
          emptyTitle="Nothing sent"
          emptyMessage="No message on this page matches the state filter. Clear the filter or move back a page."
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
export class NotificationLogComponent {
  private readonly api = inject(NotificationsApi);

  readonly states = STATES;
  readonly state = signal('');
  readonly page = signal<Page<NotificationLogEntry>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly rows = computed(() => {
    const state = this.state();
    const items = this.page().items;
    return state === '' ? items : items.filter((entry) => entry.state === state);
  });

  readonly filterHint = computed(() =>
    this.state() === ''
      ? 'Applies to the page currently loaded.'
      : `${this.rows().length} of ${this.page().items.length} on this page.`,
  );

  readonly columns: TableColumn<NotificationLogEntry>[] = [
    {
      key: 'sentAt',
      label: 'Sent',
      width: '170px',
      value: (row) => formatStamp(row.sentAt),
    },
    { key: 'channel', label: 'Channel', pill: true, width: '110px' },
    { key: 'template', label: 'Template', width: '200px' },
    { key: 'recipient', label: 'Recipient' },
    { key: 'state', label: 'State', pill: true, width: '120px' },
    { key: 'detail', label: 'Detail' },
  ];

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.log(this.page().page).subscribe({
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

  clear(): void {
    this.state.set('');
  }
}

function formatStamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}
