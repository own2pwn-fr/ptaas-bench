import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';

import { toApiError } from '../../core/api/api-error.util';
import { IntegrationsApi } from '../../core/api/integrations.api';
import { ApiError } from '../../core/models/api-error.model';
import { ProbeResult } from '../../core/models/domain.model';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  PillTone,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';
import { IntegrationsTabsComponent } from './integrations-tabs.component';

interface Attempt {
  endpoint: string;
  status: number;
  elapsedMs: number;
  at: string;
}

/** Kept short on purpose: this is a working note during a call, not an activity log. */
const HISTORY_LIMIT = 5;

/**
 * Delivery check used by the service desk while a partner is being set up.
 *
 * The partner gives an endpoint over the phone, the desk calls it once and reads the
 * answer back to them; the audit trail keeps the long-term record.
 */
@Component({
  selector: 'mrd-webhook-probe',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    EmptyStateComponent,
    ErrorBannerComponent,
    FieldErrorComponent,
    IntegrationsTabsComponent,
    PageHeaderComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Delivery check"
        subtitle="Confirm a partner receiver answers before the first manifest is sent"
      />

      <mrd-integrations-tabs />

      <mrd-error-banner
        [error]="failure()"
        heading="The delivery check did not complete"
        [retryable]="false"
        (dismiss)="failure.set(null)"
      />

      <div class="grid grid--2">
        <section class="card">
          <div class="card__header"><h2>Endpoint</h2></div>
          <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
            <div class="card__body">
              <div class="field">
                <label for="probe-endpoint">Receiver address</label>
                <input
                  id="probe-endpoint"
                  type="text"
                  formControlName="endpoint"
                  placeholder="https://edi.halvard-terminals.example/hooks/consignment"
                />
                <mrd-field-error
                  [control]="form.controls.endpoint"
                  label="Receiver address"
                  [submitted]="submitted()"
                />
              </div>
              <p class="muted small">
                Meridian calls the address once with an empty delivery so the partner can
                confirm their receiver is reachable and answers. Nothing is stored on the
                connection and no consignment data leaves the group.
              </p>
            </div>
            <div class="card__footer row">
              <button type="submit" class="btn btn--primary" [disabled]="busy()">
                {{ busy() ? 'Sending…' : 'Send a test delivery' }}
              </button>
              <span class="spacer"></span>
              <span class="muted small">Ask the partner to watch their inbound log</span>
            </div>
          </form>
        </section>

        <section class="card">
          <div class="card__header">
            <h2>What came back</h2>
            @if (result(); as answer) {
              <span class="muted small">{{ answer.elapsedMs }} ms</span>
            }
          </div>
          @if (busy()) {
            <div class="card__body"><mrd-skeleton [rows]="4" /></div>
          } @else if (result()) {
            @let answer = result()!;
            <div class="card__body stack">
              <dl class="detail">
                <dt>HTTP status</dt>
                <dd>
                  <mrd-status-pill [status]="answer.status.toString()" [tone]="tone(answer.status)" />
                </dd>
                <dt>Round trip</dt>
                <dd>{{ answer.elapsedMs }} ms</dd>
                <dt>Content type</dt>
                <dd class="mono">{{ answer.contentType || '—' }}</dd>
                <dt>Endpoint</dt>
                <dd class="mono">{{ answer.endpoint }}</dd>
              </dl>
              <div>
                <p class="field__hint">Body returned by the partner</p>
                <pre class="payload">{{ answer.body }}</pre>
              </div>
            </div>
          } @else {
            <mrd-empty-state
              title="No delivery sent yet"
              message="Enter the address the partner gave you and send one delivery."
            />
          }
        </section>
      </div>

      <section class="card recent">
        <div class="card__header">
          <h2>Recent attempts</h2>
          <span class="muted small">Last {{ historyLimit }} in this sitting</span>
        </div>
        @if (history().length === 0) {
          <mrd-empty-state
            title="Nothing checked yet"
            message="Attempts made on this screen are listed here until you leave it."
          />
        } @else {
          <table class="data">
            <thead>
              <tr>
                <th scope="col">Endpoint</th>
                <th scope="col">Status</th>
                <th scope="col" class="numeric">Round trip</th>
                <th scope="col">At</th>
              </tr>
            </thead>
            <tbody>
              @for (attempt of history(); track $index) {
                <tr>
                  <td class="mono">{{ attempt.endpoint }}</td>
                  <td>
                    <mrd-status-pill
                      [status]="attempt.status.toString()"
                      [tone]="tone(attempt.status)"
                    />
                  </td>
                  <td class="numeric">{{ attempt.elapsedMs }} ms</td>
                  <td>{{ attempt.at }}</td>
                </tr>
              }
            </tbody>
          </table>
        }
      </section>
    </div>
  `,
  styles: [
    `
      .recent {
        margin-top: 16px;
      }

      td.mono {
        word-break: break-all;
      }
    `,
  ],
})
export class WebhookProbeComponent {
  private readonly api = inject(IntegrationsApi);
  private readonly fb = inject(FormBuilder);

  readonly historyLimit = HISTORY_LIMIT;
  readonly form = this.fb.nonNullable.group({
    endpoint: ['', [Validators.required]],
  });

  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly failure = signal<ApiError | null>(null);
  readonly result = signal<ProbeResult | null>(null);
  readonly history = signal<Attempt[]>([]);

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);

    if (this.form.invalid || this.busy()) {
      return;
    }

    const endpoint = this.form.getRawValue().endpoint;
    this.busy.set(true);

    this.api.probeWebhook(endpoint).subscribe({
      next: (answer) => {
        this.busy.set(false);
        this.result.set(answer);
        this.history.update((entries) =>
          [
            {
              endpoint: answer.endpoint || endpoint,
              status: answer.status,
              elapsedMs: answer.elapsedMs,
              at: new Date().toLocaleTimeString('en-GB'),
            },
            ...entries,
          ].slice(0, HISTORY_LIMIT),
        );
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.result.set(null);
        this.failure.set(toApiError(error));
      },
    });
  }

  /** Pills are keyed on words elsewhere, so HTTP statuses get their tone here. */
  tone(status: number): PillTone {
    if (status >= 200 && status < 300) {
      return 'good';
    }
    if (status >= 300 && status < 400) {
      return 'info';
    }
    if (status >= 400 && status < 500) {
      return 'warn';
    }
    return 'bad';
  }
}
