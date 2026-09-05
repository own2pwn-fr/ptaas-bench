import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';

import { toApiError } from '../../core/api/api-error.util';
import { IntegrationsApi } from '../../core/api/integrations.api';
import { ApiError } from '../../core/models/api-error.model';
import { Integration } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  EmptyStateComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  PaginationComponent,
  SkeletonComponent,
  TableColumn,
} from '../../shared';
import { IntegrationsTabsComponent } from './integrations-tabs.component';

/** Shortest key any of the group's partners issue; anything below is a typing slip. */
const MINIMUM_SECRET_LENGTH = 12;

/**
 * Where a partner's key, password or shared secret is handed to the API.
 *
 * The console posts the secret once and keeps nothing: the API stores it against the
 * connection and only ever answers whether one is held.
 */
@Component({
  selector: 'mrd-credential-store',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    DataTableComponent,
    EmptyStateComponent,
    ErrorBannerComponent,
    FieldErrorComponent,
    IntegrationsTabsComponent,
    PageHeaderComponent,
    PaginationComponent,
    SkeletonComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Partner credentials"
        subtitle="Hand a partner's key to the API and let it hold the only copy"
      />

      <mrd-integrations-tabs />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <div class="grid grid--2">
        <section class="card">
          <div class="card__header"><h2>Store a credential</h2></div>
          @if (loading()) {
            <div class="card__body"><mrd-skeleton [rows]="3" /></div>
          } @else if (connections().length === 0) {
            <mrd-empty-state
              title="No connections yet"
              message="Add a partner connection first, then come back to store its key."
            />
          } @else {
            <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
              <div class="card__body">
                <div class="field">
                  <label for="credential-connection">Connection</label>
                  <select id="credential-connection" formControlName="integrationId">
                    <option value="">Choose a connection</option>
                    @for (connection of connections(); track connection.id) {
                      <option [value]="connection.id">
                        {{ connection.name }} · {{ connection.kind }}
                      </option>
                    }
                  </select>
                  <mrd-field-error
                    [control]="form.controls.integrationId"
                    label="Connection"
                    [submitted]="submitted()"
                  />
                </div>

                <div class="field">
                  <label for="credential-secret">Secret</label>
                  <div class="secret">
                    <input
                      id="credential-secret"
                      [type]="revealed() ? 'text' : 'password'"
                      autocomplete="off"
                      spellcheck="false"
                      formControlName="secret"
                    />
                    <button type="button" class="btn btn--sm" (click)="toggleReveal()">
                      {{ revealed() ? 'Hide' : 'Show' }}
                    </button>
                  </div>
                  <p class="field__hint">{{ strength() }}</p>
                  <mrd-field-error
                    [control]="form.controls.secret"
                    label="Secret"
                    [submitted]="submitted()"
                  />
                </div>

                @if (stored()) {
                  <div class="result">
                    <strong>Credential stored.</strong>
                    <span class="muted small">
                      The API holds this secret against the connection and never shows it again.
                      If it is lost, ask the partner to issue a new one.
                    </span>
                  </div>
                }
              </div>
              <div class="card__footer row">
                <button type="submit" class="btn btn--primary" [disabled]="busy()">
                  {{ busy() ? 'Storing…' : 'Store credential' }}
                </button>
                <span class="spacer"></span>
                <span class="muted small">At least {{ minimumLength }} characters</span>
              </div>
            </form>
          }
        </section>

        <section class="card">
          <div class="card__header"><h2>Before you paste</h2></div>
          <div class="card__body">
            <p class="muted small">
              Keys arrive with the partner's onboarding sheet, usually from their EDI desk.
              Store the key against the connection it belongs to — a key pasted onto the wrong
              terminal feed shows up as a run of rejected manifests at quarter end, not as an
              obvious failure.
            </p>
            <p class="muted small">
              Rotations follow the partner's own calendar. When they send a new key, store it
              here and the previous one stops being used on the next delivery.
            </p>
          </div>
        </section>
      </div>

      <section class="card connections">
        <div class="card__header">
          <h2>Connections</h2>
          <span class="muted small">{{ page().total }} in total</span>
        </div>
        <mrd-data-table
          [columns]="columns"
          [rows]="page().items"
          [loading]="loading()"
          emptyTitle="No partner connections"
          emptyMessage="Nothing exchanges messages with Meridian yet."
        />
        <mrd-pagination
          [page]="page().page"
          [size]="page().size"
          [total]="page().total"
          (pageChange)="goToPage($event)"
        />
      </section>
    </div>
  `,
  styles: [
    `
      .secret {
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .result {
        display: flex;
        flex-direction: column;
        gap: 2px;
        padding: 10px 12px;
        background: var(--mrd-good-soft);
        border-radius: var(--mrd-radius-sm);
      }

      .connections {
        margin-top: 16px;
      }
    `,
  ],
})
export class CredentialStoreComponent {
  private readonly api = inject(IntegrationsApi);
  private readonly fb = inject(FormBuilder);

  readonly minimumLength = MINIMUM_SECRET_LENGTH;
  readonly page = signal<Page<Integration>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);
  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly stored = signal(false);
  readonly revealed = signal(false);

  readonly form = this.fb.nonNullable.group({
    integrationId: ['', [Validators.required]],
    secret: ['', [Validators.required, Validators.minLength(MINIMUM_SECRET_LENGTH)]],
  });

  readonly connections = computed(() => this.page().items);

  /** Mirrors the secret control so the hint can be a computed signal like the rest. */
  private readonly secretValue = signal('');

  readonly strength = computed(() => describeStrength(this.secretValue()));

  readonly columns: TableColumn<Integration>[] = [
    { key: 'name', label: 'Partner' },
    { key: 'kind', label: 'Kind', pill: true, width: '110px' },
    { key: 'endpoint', label: 'Endpoint', mono: true },
    { key: 'owner', label: 'Owner', width: '150px' },
    {
      key: 'lastDeliveryState',
      label: 'Delivering',
      width: '120px',
      value: (row) => ((row.lastDeliveryState ?? 'never') !== 'never' ? 'Yes' : 'Not yet'),
    },
  ];

  constructor() {
    this.load();
    this.form.controls.secret.valueChanges.subscribe((value) => this.secretValue.set(value));
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
    // As on the connection list, the API answers in a single page for now.
    this.page.update((current) => ({ ...current, page }));
    this.load();
  }

  toggleReveal(): void {
    this.revealed.update((current) => !current);
  }

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);
    this.stored.set(false);

    if (this.form.invalid || this.busy()) {
      return;
    }

    const { integrationId, secret } = this.form.getRawValue();
    this.busy.set(true);

    this.api.storeCredential(integrationId, secret).subscribe({
      next: (answer) => {
        this.busy.set(false);
        this.stored.set(answer.stored);
        this.submitted.set(false);
        this.revealed.set(false);
        this.form.controls.secret.reset('');
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }
}

/** Plain wording rather than a meter: operators paste keys they did not choose. */
function describeStrength(secret: string): string {
  if (secret.length === 0) {
    return 'Paste the key exactly as the partner sent it.';
  }
  if (secret.length < MINIMUM_SECRET_LENGTH) {
    return `Shorter than the ${MINIMUM_SECRET_LENGTH} characters partners normally issue — check for a truncated paste.`;
  }

  const families = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((family) =>
    family.test(secret),
  ).length;

  if (secret.length >= 24 && families >= 3) {
    return 'Looks like a full partner key.';
  }
  if (families >= 3) {
    return 'Acceptable, though most partner keys are longer than this.';
  }
  return 'Only one kind of character — confirm with the partner that this is the whole key.';
}
