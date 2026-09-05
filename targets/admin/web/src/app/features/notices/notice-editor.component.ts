import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { debounceTime } from 'rxjs';

import { toApiError } from '../../core/api/api-error.util';
import { NoticesApi } from '../../core/api/notices.api';
import { ApiError } from '../../core/models/api-error.model';
import { Notice, NoticeSeverity } from '../../core/models/domain.model';
import { SessionService } from '../../core/services/session.service';
import {
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  StatusPillComponent,
} from '../../shared';
import { NoticeBodyComponent } from './notice-body.component';

/** Severities the board offers, weakest first, with the wording used in the guidance. */
const SEVERITIES: ReadonlyArray<{ value: NoticeSeverity; label: string; hint: string }> = [
  { value: 'info', label: 'Information', hint: 'Routine — cut-off times, planned work' },
  { value: 'warning', label: 'Warning', hint: 'Delays or extra handling expected' },
  { value: 'critical', label: 'Critical', hint: 'Work is blocked; the duty manager is involved' },
];

/**
 * Compose a service notice.
 *
 * The body is rendered by the same component the banner uses, so what the supervisor
 * sees in the panel underneath is what every desk will see. The counters are read at
 * render time, which is why the preview shows today's figures rather than the ones that
 * will apply when the notice actually goes out.
 */
@Component({
  selector: 'mrd-notice-editor',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    ReactiveFormsModule,
    RouterLink,
    ErrorBannerComponent,
    FieldErrorComponent,
    NoticeBodyComponent,
    PageHeaderComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Post a notice"
        subtitle="Shown in the banner on every screen until it expires"
      >
        <a actions class="btn btn--sm" routerLink="/notices">Back to the board</a>
      </mrd-page-header>

      <mrd-error-banner
        [error]="failure()"
        heading="The notice was not published"
        (retry)="submit()"
        (dismiss)="failure.set(null)"
      />

      @if (published(); as record) {
        <section class="card result">
          <div class="card__header">
            <h2>Notice published</h2>
            <mrd-status-pill [status]="record.severity" />
          </div>
          <div class="card__body">
            <p>
              <strong>{{ record.title }}</strong> is live from
              {{ record.publishedFrom | date: 'd MMM y, HH:mm' }}
              @if (record.publishedTo) {
                until {{ record.publishedTo | date: 'd MMM y, HH:mm' }}.
              } @else {
                and will stay up until it is withdrawn.
              }
            </p>
            <p class="muted small">
              Desks that have already dismissed a notice today will still see this one.
            </p>
          </div>
          <div class="card__footer row">
            <a class="btn btn--sm btn--primary" routerLink="/notices">Back to the board</a>
            <button type="button" class="btn btn--sm" (click)="startAnother()">
              Post another
            </button>
          </div>
        </section>
      }

      <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
        <div class="grid grid--2">
          <section class="card">
            <div class="card__header"><h2>Notice</h2></div>
            <div class="card__body">
              <div class="field">
                <label for="notice-title">Title</label>
                <input
                  id="notice-title"
                  type="text"
                  formControlName="title"
                  maxlength="120"
                  placeholder="Customs hold at Gothenburg — export manifests delayed"
                />
                <span class="field__hint">
                  Read at a glance in the banner. {{ titleRemaining() }} characters left.
                </span>
                <mrd-field-error
                  [control]="form.controls.title"
                  label="Title"
                  [submitted]="submitted()"
                />
              </div>

              <div class="field">
                <label for="notice-body">Body</label>
                <textarea
                  id="notice-body"
                  rows="8"
                  formControlName="body"
                  [placeholder]="bodyPlaceholder"
                ></textarea>
                <span class="field__hint">{{ counterHint }}</span>
                <span class="field__hint">Worked example: {{ counterExample }}</span>
                <mrd-field-error
                  [control]="form.controls.body"
                  label="Body"
                  [submitted]="submitted()"
                />
              </div>

              <div class="field">
                <label for="notice-severity">Severity</label>
                <select id="notice-severity" formControlName="severity">
                  @for (option of severities; track option.value) {
                    <option [value]="option.value">{{ option.label }}</option>
                  }
                </select>
                <span class="field__hint">{{ severityHint() }}</span>
              </div>
            </div>
          </section>

          <section class="card">
            <div class="card__header"><h2>Publication window</h2></div>
            <div class="card__body">
              <div class="field">
                <label for="notice-from">Shown from</label>
                <input id="notice-from" type="datetime-local" formControlName="publishedFrom" />
                <span class="field__hint">
                  Defaults to now. Set it ahead for a planned cut-off or a maintenance slot.
                </span>
                <mrd-field-error
                  [control]="form.controls.publishedFrom"
                  label="Shown from"
                  [submitted]="submitted()"
                />
              </div>

              <div class="field">
                <label for="notice-to">Shown until</label>
                <input id="notice-to" type="datetime-local" formControlName="publishedTo" />
                <span class="field__hint">
                  Optional. Leave it empty and the notice stays up until it is withdrawn.
                </span>
              </div>

              <p class="muted small">
                Times are entered in your own time zone and stored in UTC, so a notice posted
                from the Aarhus desk reads the same in Rotterdam.
              </p>
            </div>
            <div class="card__footer row">
              <button type="submit" class="btn btn--primary" [disabled]="busy()">
                {{ busy() ? 'Publishing…' : 'Publish notice' }}
              </button>
              <a class="btn btn--ghost" routerLink="/notices">Cancel</a>
            </div>
          </section>
        </div>
      </form>

      <section class="card preview">
        <div class="card__header">
          <h2>Preview</h2>
          <span class="muted small">As the banner will render it</span>
        </div>
        <div class="card__body">
          @if (preview(); as draft) {
            <article class="preview__notice" [class]="'preview__notice--' + draft.severity">
              <div class="preview__head">
                <h3>{{ draft.title || 'Untitled notice' }}</h3>
                <mrd-status-pill [status]="draft.severity" />
              </div>
              <mrd-notice-body [notice]="draft" />
              <p class="muted small preview__meta">
                {{ draft.author }} · from
                {{ draft.publishedFrom | date: 'd MMM y, HH:mm' }}
                @if (draft.publishedTo) {
                  until {{ draft.publishedTo | date: 'd MMM y, HH:mm' }}
                } @else {
                  until withdrawn
                }
              </p>
            </article>
          } @else {
            <p class="muted small">
              Start typing a body and the notice will appear here exactly as the desks see it.
            </p>
          }
        </div>
      </section>
    </div>
  `,
  styles: [
    `
      .result {
        margin-bottom: 16px;
      }

      .preview {
        margin-top: 16px;
      }

      .preview__notice {
        padding: 12px 14px;
        border-left: 4px solid var(--mrd-accent);
        background: var(--mrd-accent-soft);
        border-radius: var(--mrd-radius-sm);
      }

      .preview__notice--warning {
        border-left-color: var(--mrd-amber);
        background: var(--mrd-amber-soft);
      }

      .preview__notice--critical {
        border-left-color: var(--mrd-danger);
        background: var(--mrd-danger-soft);
      }

      .preview__head {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 4px;
      }

      .preview__head h3 {
        margin: 0;
      }

      .preview__meta {
        margin: 8px 0 0;
      }
    `,
  ],
})
export class NoticeEditorComponent {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(NoticesApi);
  private readonly session = inject(SessionService);

  readonly severities = SEVERITIES;

  /** Guidance shown under the body field; kept out of the template so it renders as text. */
  readonly counterHint =
    'A notice may quote the live counters {{ queueDepth }} and {{ openApprovals }} in double braces; ' +
    'each desk sees its own figures at the moment the banner is drawn.';
  readonly counterExample =
    'Intake is running behind: {{ queueDepth }} documents still in the queue and {{ openApprovals }} approvals waiting.';
  readonly bodyPlaceholder =
    'Export manifests for Gothenburg are held at customs until 16:00. {{ queueDepth }} documents are still in the intake queue; hold new bookings for Nordkap Shipping AS until the hold clears.';

  readonly form = this.fb.nonNullable.group({
    title: ['', [Validators.required, Validators.maxLength(120)]],
    body: ['', [Validators.required]],
    severity: ['info' as NoticeSeverity, [Validators.required]],
    publishedFrom: [localInputValue(new Date()), [Validators.required]],
    publishedTo: [''],
  });

  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly failure = signal<ApiError | null>(null);
  readonly published = signal<Notice | null>(null);

  /** Snapshot the preview is built from; refreshed on a pause in typing. */
  private readonly draft = signal(this.form.getRawValue());

  readonly titleRemaining = computed(() => 120 - this.draft().title.length);

  readonly severityHint = computed(
    () => SEVERITIES.find((option) => option.value === this.draft().severity)?.hint ?? '',
  );

  readonly preview = computed<Notice | null>(() => {
    const values = this.draft();
    if (values.body.trim() === '') {
      return null;
    }

    return {
      id: 'draft',
      title: values.title,
      body: values.body,
      severity: values.severity,
      publishedFrom: toIsoStamp(values.publishedFrom) ?? new Date().toISOString(),
      publishedTo: toIsoStamp(values.publishedTo),
      author: this.session.displayName(),
    };
  });

  constructor() {
    // The body is compiled to render it, so the preview waits for a pause rather than
    // rebuilding on every keystroke.
    this.form.valueChanges.pipe(debounceTime(250)).subscribe(() => {
      this.draft.set(this.form.getRawValue());
    });
  }

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);

    if (this.form.invalid || this.busy()) {
      return;
    }

    const values = this.form.getRawValue();
    this.busy.set(true);

    this.api
      .publish({
        title: values.title,
        body: values.body,
        severity: values.severity,
        publishedFrom: toIsoStamp(values.publishedFrom) ?? new Date().toISOString(),
        publishedTo: toIsoStamp(values.publishedTo),
      })
      .subscribe({
        next: (notice) => {
          this.busy.set(false);
          this.published.set(notice);
        },
        error: (error: unknown) => {
          this.busy.set(false);
          this.failure.set(toApiError(error));
        },
      });
  }

  startAnother(): void {
    this.published.set(null);
    this.submitted.set(false);
    this.form.reset({
      title: '',
      body: '',
      severity: 'info',
      publishedFrom: localInputValue(new Date()),
      publishedTo: '',
    });
    this.draft.set(this.form.getRawValue());
  }
}

/** `datetime-local` wants a local wall-clock string, not the ISO stamp the API stores. */
function localInputValue(at: Date): string {
  const local = new Date(at.getTime() - at.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function toIsoStamp(value: string): string | null {
  if (value.trim() === '') {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}
