import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { NotificationsApi } from '../../core/api/notifications.api';
import { ApiError } from '../../core/models/api-error.model';
import { NotificationPreview, NotificationTemplate } from '../../core/models/domain.model';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';
import { NotificationsTabsComponent } from './notifications-tabs.component';

/** Where the template source comes from: the stored library, or pasted into the box. */
type TemplateSource = 'stored' | 'pasted';

/**
 * A realistic arrival notice, so the screen renders something meaningful the first
 * time it is opened. Operators normally paste the payload of a real message over it.
 */
const SAMPLE_PAYLOAD = JSON.stringify(
  {
    event: 'consignment.arrival',
    reference: 'CW-40233',
    account: {
      name: 'Nordkap Shipping AS',
      contact: 'H. Lindqvist',
      email: 'h.lindqvist@nordkap.example',
    },
    consignment: {
      vessel: 'MV Sundsvall',
      voyage: 'SUN-118W',
      origin: 'Gothenburg',
      destination: 'Rotterdam',
      eta: '2026-09-12T06:30:00Z',
      containers: 4,
      grossWeightKg: 18420,
      incoterm: 'CIF',
    },
    customsHold: false,
    raisedBy: 'M. Okonkwo',
  },
  null,
  2,
);

/**
 * Message preview.
 *
 * Renders a template against a payload without sending anything, which is how the desk
 * checks the wording of an arrival notice before an EDI partner is switched over.
 */
@Component({
  selector: 'mrd-notifications-home',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    ReactiveFormsModule,
    RouterLink,
    EmptyStateComponent,
    ErrorBannerComponent,
    FieldErrorComponent,
    NotificationsTabsComponent,
    PageHeaderComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Notifications"
        subtitle="Render a message against a payload before it goes out"
      >
        <a actions class="btn btn--sm" routerLink="/notifications/templates">Templates</a>
        <a actions class="btn btn--sm" routerLink="/notifications/log">Delivery log</a>
      </mrd-page-header>

      <mrd-notifications-tabs />

      <mrd-error-banner [error]="failure()" (retry)="submit()" (dismiss)="failure.set(null)" />

      <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
        <div class="grid grid--2">
          <section class="card">
            <div class="card__header"><h2>Message</h2></div>
            <div class="card__body">
              <div class="field">
                <label for="preview-source">Template source</label>
                <select id="preview-source" formControlName="source">
                  <option value="stored">Stored template</option>
                  <option value="pasted">Paste a body</option>
                </select>
                <span class="field__hint">
                  The render endpoint takes either the id of a stored template or the
                  template source itself.
                </span>
              </div>

              @if (source() === 'stored') {
                <div class="field">
                  <label for="preview-template">Stored template</label>
                  @if (templatesLoading()) {
                    <mrd-skeleton [rows]="1" />
                  } @else {
                    <select id="preview-template" formControlName="templateId">
                      <option value="">Choose a template…</option>
                      @for (template of templates(); track template.id) {
                        <option [value]="template.id">
                          {{ template.name }} · {{ template.channel }}
                        </option>
                      }
                    </select>
                    @if (templates().length === 0) {
                      <span class="field__hint">
                        No stored template came back. Paste a body instead.
                      </span>
                    }
                  }
                  <mrd-field-error
                    [control]="form.controls.templateId"
                    label="Stored template"
                    [submitted]="submitted()"
                  />
                </div>
              } @else {
                <div class="field">
                  <label for="preview-body">Template body</label>
                  <textarea
                    id="preview-body"
                    rows="10"
                    formControlName="templateBody"
                    [placeholder]="pastePlaceholder"
                  ></textarea>
                  <span class="field__hint">
                    Pasted bodies are rendered but never stored; save the wording in the
                    template library once the desk is happy with it.
                  </span>
                  <mrd-field-error
                    [control]="form.controls.templateBody"
                    label="Template body"
                    [submitted]="submitted()"
                  />
                </div>
              }
            </div>
          </section>

          <section class="card">
            <div class="card__header"><h2>Payload</h2></div>
            <div class="card__body">
              <div class="field">
                <label for="preview-sample">Payload</label>
                <textarea id="preview-sample" rows="16" formControlName="sample"></textarea>
                <span class="field__hint">
                  The values the message is rendered against — usually copied from a
                  consignment event.
                </span>
                <mrd-field-error
                  [control]="form.controls.sample"
                  label="Payload"
                  [submitted]="submitted()"
                />
              </div>
            </div>
            <div class="card__footer row">
              <button type="submit" class="btn btn--primary" [disabled]="busy()">
                {{ busy() ? 'Rendering…' : 'Render message' }}
              </button>
              <button type="button" class="btn btn--ghost" (click)="restoreSample()">
                Restore the arrival payload
              </button>
            </div>
          </section>
        </div>
      </form>

      <section class="card rendered">
        <div class="card__header">
          <h2>Rendered message</h2>
          @if (result(); as rendered) {
            <mrd-status-pill [status]="rendered.channel" />
          }
        </div>

        @if (busy()) {
          <div class="rendered__loading">
            <mrd-skeleton [rows]="5" />
          </div>
        } @else if (result()) {
          @let rendered = result()!;
          <div class="card__body">
            <p class="rendered__subject"><strong>Subject:</strong> {{ rendered.subject }}</p>
            <pre class="payload">{{ rendered.body }}</pre>
            <p class="muted small rendered__stamp">
              Rendered {{ rendered.renderedAt | date: 'd MMM y, HH:mm:ss' }}
            </p>
          </div>
        } @else {
          <mrd-empty-state
            title="Nothing rendered yet"
            message="Pick a template, check the payload and render the message to see how it will read."
          />
        }
      </section>
    </div>
  `,
  styles: [
    `
      .rendered {
        margin-top: 16px;
      }

      .rendered__loading {
        padding: 16px;
      }

      .rendered__subject {
        margin-bottom: 10px;
      }

      .rendered__stamp {
        margin: 8px 0 0;
      }
    `,
  ],
})
export class NotificationsHomeComponent {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(NotificationsApi);

  readonly form = this.fb.nonNullable.group({
    source: ['stored' as TemplateSource, [Validators.required]],
    templateId: ['', [Validators.required]],
    templateBody: [''],
    sample: [SAMPLE_PAYLOAD, [Validators.required]],
  });

  /** Kept out of the template so the placeholder's own braces render as written. */
  readonly pastePlaceholder =
    'Dear {{ account.contact }}, MV Sundsvall (voyage {{ consignment.voyage }}) is due in ' +
    '{{ consignment.destination }} on {{ consignment.eta }}.';

  /** Mirrors the source control so the two halves of the form redraw under OnPush. */
  readonly source = signal<TemplateSource>('stored');

  readonly templates = signal<NotificationTemplate[]>([]);
  readonly templatesLoading = signal(true);
  readonly result = signal<NotificationPreview | null>(null);
  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly failure = signal<ApiError | null>(null);

  constructor() {
    // Only the field in use is required, otherwise the form can never be submitted in
    // the other mode.
    this.form.controls.source.valueChanges.subscribe((source) => {
      this.source.set(source);
      const stored = source === 'stored';
      this.form.controls.templateId.setValidators(stored ? [Validators.required] : []);
      this.form.controls.templateBody.setValidators(stored ? [] : [Validators.required]);
      this.form.controls.templateId.updateValueAndValidity();
      this.form.controls.templateBody.updateValueAndValidity();
    });

    this.loadTemplates();
  }

  loadTemplates(): void {
    this.templatesLoading.set(true);
    this.api.templates().subscribe({
      next: (page) => {
        this.templates.set(page.items);
        this.templatesLoading.set(false);
        const first = page.items[0];
        if (first !== undefined && this.form.controls.templateId.value === '') {
          this.form.controls.templateId.setValue(first.id);
        }
      },
      error: (error: unknown) => {
        this.templatesLoading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);

    if (this.form.invalid || this.busy()) {
      return;
    }

    const values = this.form.getRawValue();
    const template = values.source === 'stored' ? values.templateId : values.templateBody;

    this.busy.set(true);
    this.api.preview(template, values.sample).subscribe({
      next: (rendered) => {
        this.busy.set(false);
        this.result.set(rendered);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  restoreSample(): void {
    this.form.controls.sample.setValue(SAMPLE_PAYLOAD);
  }
}
