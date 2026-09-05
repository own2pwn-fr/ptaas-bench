import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { RulesApi } from '../../core/api/rules.api';
import { ApiError } from '../../core/models/api-error.model';
import { RulePreviewResult } from '../../core/models/domain.model';
import {
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  StatusPillComponent,
} from '../../shared';

/**
 * A booking of the shape the engine hands a consignment rule. Kept as text rather than
 * an object so the operator can edit a single field and run the expression again.
 */
const STARTING_SAMPLE = `{
  "reference": "CW-40118",
  "customer": "Halvard Terminals SA",
  "consignment": {
    "origin": "Gothenburg",
    "destination": "Rotterdam",
    "mode": "sea",
    "vessel": "MV Sundsvall",
    "weightKg": 12480,
    "volumeCbm": 34.2,
    "etd": "2026-09-12",
    "eta": "2026-09-16"
  },
  "account": {
    "incoterm": "DAP",
    "country": "SE",
    "creditLimit": 250000,
    "currency": "EUR"
  }
}`;

interface ExpressionExample {
  expression: string;
  note: string;
}

/** Shown beside the editor; these are the shapes the desk writes most often. */
const EXAMPLES: ReadonlyArray<ExpressionExample> = [
  {
    expression: 'consignment.weightKg > 12000',
    note: 'Heavy lift handling on anything over twelve tonnes.',
  },
  {
    expression: 'account.incoterm == "DAP"',
    note: 'Delivered-at-place bookings, where we carry the onward leg.',
  },
  {
    expression: 'consignment.origin == "Gothenburg" and consignment.destination == "Rotterdam"',
    note: 'One lane only; combine terms with "and" / "or".',
  },
  {
    expression: 'consignment.mode == "sea" and account.country != "SE"',
    note: 'Cross-border sea freight, used for the customs paperwork rule.',
  },
];

/**
 * The expression preview screen.
 *
 * Rules are cheap to write and expensive to get wrong, so an author runs the expression
 * against a record here before saving it into the rule book.
 */
@Component({
  selector: 'mrd-rule-preview',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DecimalPipe,
    ReactiveFormsModule,
    RouterLink,
    ErrorBannerComponent,
    FieldErrorComponent,
    PageHeaderComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Expression preview"
        subtitle="Run a rule expression against a record before it goes into the book"
      >
        <a actions class="btn btn--sm" routerLink="/rules">Back to rules</a>
      </mrd-page-header>

      <mrd-error-banner
        [error]="failure()"
        heading="The expression could not be run"
        [retryable]="false"
        (dismiss)="failure.set(null)"
      />

      <div class="grid grid--2">
        <section class="card">
          <div class="card__header">
            <h2>Expression</h2>
            <span class="muted small">Nothing here is saved to the rule book</span>
          </div>
          <form class="card__body" [formGroup]="form" (ngSubmit)="submit()" novalidate>
            <div class="field">
              <label for="preview-expression">Rule expression</label>
              <textarea
                id="preview-expression"
                rows="3"
                formControlName="expression"
                placeholder="consignment.weightKg > 12000"
              ></textarea>
              <mrd-field-error
                [control]="form.controls.expression"
                label="Rule expression"
                [submitted]="submitted()"
              />
            </div>

            <div class="field">
              <label for="preview-sample">Record</label>
              <textarea id="preview-sample" rows="16" formControlName="sample"></textarea>
              <p class="field__hint">
                A JSON record of the kind the engine passes the rule. Edit a value and run it
                again to see where the rule stops matching.
              </p>
              <mrd-field-error
                [control]="form.controls.sample"
                label="Record"
                [submitted]="submitted()"
              />
            </div>

            <div class="row">
              <button type="submit" class="btn btn--primary" [disabled]="busy()">
                {{ busy() ? 'Running…' : 'Run expression' }}
              </button>
              <button type="button" class="btn btn--ghost" (click)="restore()">
                Restore the starting record
              </button>
            </div>
          </form>
        </section>

        <div class="stack">
          <section class="card">
            <div class="card__header"><h2>Outcome</h2></div>
            <div class="card__body">
              @if (busy()) {
                <p class="muted small">Running the expression against the record…</p>
              } @else if (result()) {
                @let outcome = result()!;
                <dl class="detail">
                  <dt>Matched</dt>
                  <dd>
                    <mrd-status-pill
                      [status]="outcome.matched ? 'matched' : 'no match'"
                      [tone]="outcome.matched ? 'good' : 'neutral'"
                    />
                  </dd>
                  <dt>Time taken</dt>
                  <dd>{{ outcome.elapsedMs | number: '1.0-1' }} ms</dd>
                </dl>

                <h3 class="outcome-heading">Value returned</h3>
                <pre class="payload">{{ outcome.value }}</pre>

                <h3 class="outcome-heading">Notes</h3>
                @if (outcome.notes.length === 0) {
                  <p class="muted small">The engine had nothing to add.</p>
                } @else {
                  <ul class="notes small">
                    @for (note of outcome.notes; track $index) {
                      <li>{{ note }}</li>
                    }
                  </ul>
                }
              } @else {
                <p class="muted small">
                  Run an expression to see whether it matches, what it returns and how long the
                  engine spent on it.
                </p>
              }
            </div>
          </section>

          <section class="card">
            <div class="card__header"><h2>Writing an expression</h2></div>
            <div class="card__body">
              <p class="small muted">
                An expression reads the record with dotted names and compares it with
                <span class="mono">==</span>, <span class="mono">!=</span>,
                <span class="mono">&gt;</span> or <span class="mono">&lt;</span>. Combine terms
                with <span class="mono">and</span> / <span class="mono">or</span>. Text is quoted;
                weights and amounts are not.
              </p>
              <ul class="examples">
                @for (example of examples; track example.expression) {
                  <li>
                    <button type="button" class="example" (click)="useExample(example.expression)">
                      <code>{{ example.expression }}</code>
                    </button>
                    <span class="muted small">{{ example.note }}</span>
                  </li>
                }
              </ul>
            </div>
          </section>
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .outcome-heading {
        margin: 16px 0 6px;
      }

      .notes {
        margin: 0;
        padding-left: 18px;
      }

      .examples {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 10px;
      }

      .example {
        display: block;
        width: 100%;
        padding: 6px 8px;
        text-align: left;
        background: var(--mrd-surface-sunken);
        border: 1px solid var(--mrd-line);
        border-radius: var(--mrd-radius-sm);
        cursor: pointer;
      }

      .example:hover {
        background: var(--mrd-accent-soft);
      }
    `,
  ],
})
export class RulePreviewComponent {
  private readonly api = inject(RulesApi);
  private readonly fb = inject(FormBuilder);
  private readonly route = inject(ActivatedRoute);

  readonly examples = EXAMPLES;

  readonly form = this.fb.nonNullable.group({
    expression: ['', [Validators.required, Validators.minLength(3)]],
    sample: [STARTING_SAMPLE, [Validators.required]],
  });

  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly failure = signal<ApiError | null>(null);
  readonly result = signal<RulePreviewResult | null>(null);

  constructor() {
    // The rule detail screen sends the expression across in the query string.
    const carried = this.route.snapshot.queryParamMap.get('expression');
    if (carried) {
      this.form.controls.expression.setValue(carried);
    }
  }

  useExample(expression: string): void {
    this.form.controls.expression.setValue(expression);
  }

  restore(): void {
    this.form.controls.sample.setValue(STARTING_SAMPLE);
  }

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);

    if (this.form.invalid || this.busy()) {
      return;
    }

    const { expression, sample } = this.form.getRawValue();
    this.busy.set(true);

    this.api.preview(expression, sample).subscribe({
      next: (outcome) => {
        this.busy.set(false);
        this.result.set(outcome);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.result.set(null);
        this.failure.set(toApiError(error));
      },
    });
  }
}
