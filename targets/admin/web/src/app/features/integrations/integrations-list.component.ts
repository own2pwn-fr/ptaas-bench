import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';

import { toApiError } from '../../core/api/api-error.util';
import { IntegrationsApi } from '../../core/api/integrations.api';
import { ApiError } from '../../core/models/api-error.model';
import { Integration } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';
import { IntegrationsTabsComponent } from './integrations-tabs.component';

/** Connection kinds the group's partners are set up with. */
const KINDS: ReadonlyArray<{ value: Integration['kind']; label: string }> = [
  { value: 'edi', label: 'EDI message feed' },
  { value: 'webhook', label: 'Webhook' },
  { value: 'sftp', label: 'SFTP drop' },
  { value: 'erp', label: 'ERP link' },
];

/** Partner endpoints are called from the group network, so plain HTTP is not accepted. */
const HTTPS_ENDPOINT = /^https:\/\/[^\s]+$/;

/**
 * The list of partner connections: carriers, terminals and customer ERPs that exchange
 * consignment and invoice messages with Meridian.
 */
@Component({
  selector: 'mrd-integrations-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    DataTableComponent,
    ErrorBannerComponent,
    FieldErrorComponent,
    IntegrationsTabsComponent,
    PageHeaderComponent,
    PaginationComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Partner connections"
        subtitle="Carrier feeds, terminal webhooks and customer ERP links"
      >
        <button actions type="button" class="btn btn--sm btn--primary" (click)="togglePanel()">
          {{ panelOpen() ? 'Close' : 'Connect a partner' }}
        </button>
      </mrd-page-header>

      <mrd-integrations-tabs />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      @if (panelOpen()) {
        <section class="card connect">
          <div class="card__header">
            <h2>Connect a partner</h2>
            <span class="muted small">Details come from the partner's onboarding sheet</span>
          </div>
          <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
            <div class="card__body">
              <div class="grid grid--2">
                <div class="field">
                  <label for="connection-name">Partner name</label>
                  <input
                    id="connection-name"
                    type="text"
                    formControlName="name"
                    placeholder="Halvard Terminals SA"
                  />
                  <mrd-field-error
                    [control]="form.controls.name"
                    label="Partner name"
                    [submitted]="submitted()"
                  />
                </div>

                <div class="field">
                  <label for="connection-kind">Connection kind</label>
                  <select id="connection-kind" formControlName="kind">
                    @for (kind of kinds; track kind.value) {
                      <option [value]="kind.value">{{ kind.label }}</option>
                    }
                  </select>
                  <p class="field__hint">
                    EDI feeds carry manifests, webhooks carry consignment status changes.
                  </p>
                </div>

                <div class="field">
                  <label for="connection-endpoint">Endpoint</label>
                  <input
                    id="connection-endpoint"
                    type="text"
                    formControlName="endpoint"
                    placeholder="https://edi.halvard-terminals.example/hooks/consignment"
                  />
                  <p class="field__hint">Must start with https:// — partner traffic is never sent in the clear.</p>
                  <mrd-field-error
                    [control]="form.controls.endpoint"
                    label="Endpoint"
                    [submitted]="submitted()"
                  />
                </div>

                <div class="field">
                  <label for="connection-owner">Owner</label>
                  <input
                    id="connection-owner"
                    type="text"
                    formControlName="owner"
                    placeholder="H. Lindqvist"
                  />
                  <p class="field__hint">
                    Whoever the service desk calls when deliveries start failing.
                  </p>
                </div>
              </div>

              @if (created(); as record) {
                <div class="result">
                  <strong>{{ record.name }} added.</strong>
                  <span class="muted small">
                    The connection starts disabled until the partner confirms their receiver;
                    run a delivery check before enabling it.
                  </span>
                </div>
              }
            </div>
            <div class="card__footer row">
              <button type="submit" class="btn btn--primary" [disabled]="busy()">
                {{ busy() ? 'Saving…' : 'Add connection' }}
              </button>
              <button type="button" class="btn btn--ghost" (click)="togglePanel()">Cancel</button>
            </div>
          </form>
        </section>
      }

      <div class="card">
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          emptyTitle="No partner connections"
          emptyMessage="Nothing exchanges messages with Meridian yet. Add the first partner from the header."
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
      .connect {
        margin-bottom: 16px;
      }

      .result {
        display: flex;
        flex-direction: column;
        gap: 2px;
        margin-top: 4px;
        padding: 10px 12px;
        background: var(--mrd-good-soft);
        border-radius: var(--mrd-radius-sm);
      }
    `,
  ],
})
export class IntegrationsListComponent {
  private readonly api = inject(IntegrationsApi);
  private readonly fb = inject(FormBuilder);

  readonly kinds = KINDS;
  readonly page = signal<Page<Integration>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);
  readonly panelOpen = signal(false);
  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly created = signal<Integration | null>(null);

  readonly form = this.fb.nonNullable.group({
    name: ['', [Validators.required]],
    kind: ['webhook' as Integration['kind']],
    endpoint: ['', [Validators.required, Validators.pattern(HTTPS_ENDPOINT)]],
    owner: [''],
  });

  readonly columns: TableColumn<Integration>[] = [
    { key: 'name', label: 'Partner' },
    { key: 'kind', label: 'Kind', pill: true, width: '110px' },
    { key: 'endpoint', label: 'Endpoint', mono: true },
    { key: 'owner', label: 'Owner', width: '150px' },
    {
      key: 'lastDeliveryAt',
      label: 'Last delivery',
      width: '180px',
      value: (row) => formatMoment(row.lastDeliveryAt),
    },
    {
      key: 'lastDeliveryState',
      label: 'Delivery state',
      pill: true,
      width: '130px',
      value: (row) => row.lastDeliveryState ?? 'never',
    },
    {
      key: 'enabled',
      label: 'Status',
      pill: true,
      width: '110px',
      value: (row) => (row.enabled ? 'enabled' : 'disabled'),
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
    // The API returns every connection in one page today; the pager is here so the list
    // behaves like the rest of the console once the partner book grows.
    this.page.update((current) => ({ ...current, page }));
    this.load();
  }

  togglePanel(): void {
    const next = !this.panelOpen();
    this.panelOpen.set(next);
    if (!next) {
      this.reset();
    }
  }

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);

    if (this.form.invalid || this.busy()) {
      return;
    }

    this.busy.set(true);
    this.api.create(this.form.getRawValue()).subscribe({
      next: (record) => {
        this.busy.set(false);
        this.created.set(record);
        this.submitted.set(false);
        this.form.reset({ name: '', kind: 'webhook', endpoint: '', owner: '' });
        // Show it straight away rather than re-reading the whole list for one row.
        this.page.update((current) => ({
          ...current,
          items: [record, ...current.items],
          total: current.total + 1,
        }));
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  private reset(): void {
    this.submitted.set(false);
    this.created.set(null);
    this.form.reset({ name: '', kind: 'webhook', endpoint: '', owner: '' });
  }
}

/** Table cells are plain strings, so timestamps are formatted here instead of by a pipe. */
function formatMoment(value: string | undefined): string {
  if (!value) {
    return '';
  }
  const moment = new Date(value);
  return Number.isNaN(moment.getTime())
    ? value
    : moment.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}
