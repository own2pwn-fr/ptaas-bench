import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { ExportsApi } from '../../core/api/exports.api';
import { ApiError } from '../../core/models/api-error.model';
import { ExportJob, ExportTemplate, RenderResult } from '../../core/models/domain.model';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';
import { ExportsTabsComponent } from './exports-tabs.component';

/**
 * Marks the last entry of the layout chooser.
 *
 * Most customers take one of the layouts the documentation team maintains; a handful
 * insist on their own paperwork, and for those the operator pastes the layout the
 * customer supplied instead of picking a stored name.
 */
const CUSTOM_LAYOUT = '__custom__';

/** File formats the extract service writes. */
const BULK_FORMATS = [
  { value: 'csv', label: 'CSV — one row per consignment line' },
  { value: 'xlsx', label: 'XLSX — workbook, one sheet per account' },
  { value: 'xml', label: 'XML — partner interchange format' },
  { value: 'pdf', label: 'PDF — printable listing' },
];

/** The row count operations settled on after the quarter-end runs kept timing out. */
const DEFAULT_ROWS = 5000;

/** Render one statement, or ask for a bulk extract of the whole ledger. */
@Component({
  selector: 'mrd-document-render',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    DecimalPipe,
    ReactiveFormsModule,
    RouterLink,
    EmptyStateComponent,
    ErrorBannerComponent,
    ExportsTabsComponent,
    FieldErrorComponent,
    PageHeaderComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Render and extract"
        subtitle="Customer paperwork from a stored layout, and bulk extracts for the finance desk"
      >
        <a actions class="btn btn--sm" routerLink="/exports/history">Export history</a>
      </mrd-page-header>

      <mrd-exports-tabs />

      <mrd-error-banner
        [error]="failure()"
        heading="The stored layouts could not be listed"
        (retry)="loadTemplates()"
        (dismiss)="failure.set(null)"
      />

      <div class="grid grid--2">
        <section class="card">
          <div class="card__header">
            <h2>Render a statement</h2>
            <a class="small" routerLink="/exports/templates">Stored layouts</a>
          </div>

          <form [formGroup]="renderForm" (ngSubmit)="render()" novalidate>
            <div class="card__body">
              <div class="field">
                <label for="statement-id">Statement</label>
                <input
                  id="statement-id"
                  type="text"
                  formControlName="statementId"
                  placeholder="CW-40118-2026-07"
                  autocomplete="off"
                />
                <p class="field__hint">
                  Account reference and accounting period, as printed on the statement.
                </p>
                <mrd-field-error
                  [control]="renderForm.controls.statementId"
                  label="Statement"
                  [submitted]="renderSubmitted()"
                />
              </div>

              @if (templatesLoading()) {
                <mrd-skeleton [rows]="3" />
              } @else {
                @if (layouts().length === 0) {
                  <mrd-empty-state
                    title="No stored layouts"
                    message="The documentation team has not published a layout for this account yet. Paste the customer's own layout below."
                  />
                } @else {
                  <div class="field">
                    <label for="layout">Layout</label>
                    <select id="layout" formControlName="layout">
                      <option value="">Choose a layout…</option>
                      @for (layout of layouts(); track layout.id) {
                        <option [value]="layout.stylesheet">
                          {{ layout.name }} · {{ layout.format }}
                        </option>
                      }
                      <option [value]="customValue">Custom layout…</option>
                    </select>
                    <p class="field__hint">
                      Stored layouts are maintained by the documentation team; pick the one the
                      customer signed off.
                    </p>
                    <mrd-field-error
                      [control]="renderForm.controls.layout"
                      label="Layout"
                      [submitted]="renderSubmitted()"
                    />
                  </div>
                }

                @if (usingCustomLayout()) {
                  <div class="field">
                    <label for="custom-layout">Custom layout</label>
                    <textarea
                      id="custom-layout"
                      rows="10"
                      formControlName="customLayout"
                      spellcheck="false"
                      placeholder="Paste the layout the customer supplied."
                    ></textarea>
                    <p class="field__hint">
                      Sent instead of a stored name. If the customer sends the same layout every
                      period, ask the documentation team to store it.
                    </p>
                    <mrd-field-error
                      [control]="renderForm.controls.customLayout"
                      label="Custom layout"
                      [submitted]="renderSubmitted()"
                    />
                  </div>
                }
              }

              <mrd-error-banner
                [error]="renderFailure()"
                heading="The statement was not rendered"
                [retryable]="false"
                (dismiss)="renderFailure.set(null)"
              />
            </div>

            <div class="card__footer row">
              <span class="spacer"></span>
              <button type="submit" class="btn btn--sm btn--primary" [disabled]="rendering()">
                {{ rendering() ? 'Rendering…' : 'Render statement' }}
              </button>
            </div>
          </form>

          @if (rendering()) {
            <div class="card__body">
              <mrd-skeleton [rows]="4" />
            </div>
          } @else if (rendered()) {
            @let result = rendered()!;
            <div class="card__body">
              <dl class="detail">
                <dt>Job</dt>
                <dd class="mono">{{ result.jobId }}</dd>
                <dt>Content type</dt>
                <dd class="mono">{{ result.contentType }}</dd>
                <dt>Size</dt>
                <dd>{{ result.bytes | number }} bytes</dd>
              </dl>
              <h3 class="small panel__title">Preview</h3>
              <pre class="payload">{{ result.preview }}</pre>
            </div>
          }
        </section>

        <section class="card">
          <div class="card__header">
            <h2>Bulk extract</h2>
            <a class="small" routerLink="/exports/history">Recent extracts</a>
          </div>

          <form [formGroup]="batchForm" (ngSubmit)="startBatch()" novalidate>
            <div class="card__body">
              <div class="field">
                <label for="format">Format</label>
                <select id="format" formControlName="format">
                  @for (option of formats; track option.value) {
                    <option [value]="option.value">{{ option.label }}</option>
                  }
                </select>
                <mrd-field-error
                  [control]="batchForm.controls.format"
                  label="Format"
                  [submitted]="batchSubmitted()"
                />
              </div>

              <div class="field">
                <label for="rows">Rows per file</label>
                <input id="rows" type="number" min="1" step="100" formControlName="rows" />
                <p class="field__hint">
                  Operations normally use {{ defaultRows | number }}. Larger files are slower to
                  open on the terminal machines and rarely worth it.
                </p>
                <mrd-field-error
                  [control]="batchForm.controls.rows"
                  label="Rows per file"
                  [submitted]="batchSubmitted()"
                />
              </div>

              <mrd-error-banner
                [error]="batchFailure()"
                heading="The extract was not queued"
                [retryable]="false"
                (dismiss)="batchFailure.set(null)"
              />
            </div>

            <div class="card__footer row">
              <span class="muted small">Extracts run on the reporting replica.</span>
              <span class="spacer"></span>
              <button type="submit" class="btn btn--sm btn--primary" [disabled]="batching()">
                {{ batching() ? 'Queueing…' : 'Start extract' }}
              </button>
            </div>
          </form>

          @if (batching()) {
            <div class="card__body">
              <mrd-skeleton [rows]="4" />
            </div>
          } @else if (job()) {
            @let queued = job()!;
            <div class="card__body">
              <dl class="detail">
                <dt>Job</dt>
                <dd class="mono">{{ queued.id }}</dd>
                <dt>State</dt>
                <dd><mrd-status-pill [status]="queued.state" /></dd>
                <dt>Requested at</dt>
                <dd>{{ queued.requestedAt | date: 'd MMM y, HH:mm:ss' }}</dd>
                <dt>Format</dt>
                <dd>{{ queued.format }}</dd>
                <dt>Rows per file</dt>
                <dd>{{ queued.rows | number }}</dd>
              </dl>
              <p class="muted small">
                The artefact appears in the export history once the run finishes.
              </p>
            </div>
          }
        </section>
      </div>
    </div>
  `,
  styles: [
    `
      .panel__title {
        margin: 16px 0 6px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--mrd-ink-faint);
      }
    `,
  ],
})
export class DocumentRenderComponent {
  private readonly api = inject(ExportsApi);
  private readonly fb = inject(FormBuilder);

  readonly customValue = CUSTOM_LAYOUT;
  readonly formats = BULK_FORMATS;
  readonly defaultRows = DEFAULT_ROWS;

  readonly renderForm = this.fb.nonNullable.group({
    statementId: [
      '',
      [Validators.required, Validators.minLength(6), Validators.pattern(/^[A-Za-z0-9-]+$/)],
    ],
    layout: ['', [Validators.required]],
    customLayout: [''],
  });

  readonly batchForm = this.fb.nonNullable.group({
    format: ['csv', [Validators.required]],
    rows: [DEFAULT_ROWS, [Validators.required, Validators.min(1), Validators.max(200000)]],
  });

  readonly layouts = signal<ExportTemplate[]>([]);
  readonly templatesLoading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly renderSubmitted = signal(false);
  readonly rendering = signal(false);
  readonly renderFailure = signal<ApiError | null>(null);
  readonly rendered = signal<RenderResult | null>(null);

  readonly batchSubmitted = signal(false);
  readonly batching = signal(false);
  readonly batchFailure = signal<ApiError | null>(null);
  readonly job = signal<ExportJob | null>(null);

  private readonly layoutChoice = signal('');
  readonly usingCustomLayout = computed(() => this.layoutChoice() === CUSTOM_LAYOUT);

  constructor() {
    this.renderForm.controls.layout.valueChanges.subscribe((value) => {
      this.layoutChoice.set(value);

      // The pasted layout is only required while the custom option is the one selected.
      const control = this.renderForm.controls.customLayout;
      if (value === CUSTOM_LAYOUT) {
        control.addValidators(Validators.required);
      } else {
        control.removeValidators(Validators.required);
      }
      control.updateValueAndValidity({ emitEvent: false });
    });

    this.loadTemplates();
  }

  loadTemplates(): void {
    this.templatesLoading.set(true);
    this.failure.set(null);
    this.api.templates().subscribe({
      next: (page) => {
        this.layouts.set(page.items);
        this.templatesLoading.set(false);

        // Nothing stored for this account: the only way to render is a supplied layout.
        if (page.items.length === 0) {
          this.renderForm.controls.layout.setValue(CUSTOM_LAYOUT);
        }
      },
      error: (error: unknown) => {
        this.templatesLoading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  render(): void {
    this.renderSubmitted.set(true);
    this.renderFailure.set(null);

    if (this.renderForm.invalid || this.rendering()) {
      return;
    }

    const { statementId, layout, customLayout } = this.renderForm.getRawValue();
    const stylesheet = layout === CUSTOM_LAYOUT ? customLayout : layout;

    this.rendering.set(true);
    this.api.render(statementId, stylesheet).subscribe({
      next: (result) => {
        this.rendering.set(false);
        this.rendered.set(result);
      },
      error: (error: unknown) => {
        this.rendering.set(false);
        this.renderFailure.set(toApiError(error));
      },
    });
  }

  startBatch(): void {
    this.batchSubmitted.set(true);
    this.batchFailure.set(null);

    if (this.batchForm.invalid || this.batching()) {
      return;
    }

    const { format, rows } = this.batchForm.getRawValue();

    this.batching.set(true);
    this.api.batch(format, rows).subscribe({
      next: (queued) => {
        this.batching.set(false);
        this.job.set(queued);
      },
      error: (error: unknown) => {
        this.batching.set(false);
        this.batchFailure.set(toApiError(error));
      },
    });
  }
}
