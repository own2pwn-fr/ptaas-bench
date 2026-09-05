import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { RulesApi } from '../../core/api/rules.api';
import { ApiError } from '../../core/models/api-error.model';
import { RuleSummary } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  FilterBarComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';

type RuleScope = RuleSummary['scope'];

/** The scopes the operations engine knows, in the order the desk thinks about them. */
const SCOPES: ReadonlyArray<{ value: RuleScope; label: string }> = [
  { value: 'consignment', label: 'Consignment' },
  { value: 'invoice', label: 'Invoice' },
  { value: 'account', label: 'Account' },
  { value: 'document', label: 'Document' },
];

const STAMP = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

function stamp(value: string | undefined): string {
  if (!value) {
    return '';
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : STAMP.format(parsed);
}

/**
 * Expressions are authored on one line but can run long; the grid shows the head of
 * the expression and the detail screen carries the whole thing.
 */
function shorten(expression: string, limit = 60): string {
  const single = expression.replace(/\s+/g, ' ').trim();
  return single.length > limit ? `${single.slice(0, limit - 1)}…` : single;
}

/** Every routing and pricing rule the operations engine runs, newest change first. */
@Component({
  selector: 'mrd-rules-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    RouterLink,
    DataTableComponent,
    ErrorBannerComponent,
    FieldErrorComponent,
    FilterBarComponent,
    PageHeaderComponent,
    PaginationComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Rules"
        subtitle="Expressions the operations engine runs on every booking, invoice and document"
      >
        <a actions class="btn btn--sm" routerLink="/rules/preview">Expression preview</a>
        <button actions type="button" class="btn btn--sm btn--primary" (click)="toggleCreate()">
          {{ creating() ? 'Cancel' : 'New rule' }}
        </button>
      </mrd-page-header>

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      @if (creating()) {
        <section class="card create">
          <div class="card__header">
            <h2>New rule</h2>
            <span class="muted small">Saved rules start running on the next booking</span>
          </div>
          <form class="card__body" [formGroup]="form" (ngSubmit)="submit()" novalidate>
            <div class="grid grid--2">
              <div class="field">
                <label for="rule-name">Name</label>
                <input
                  id="rule-name"
                  type="text"
                  formControlName="name"
                  placeholder="Heavy lift surcharge, Gothenburg–Rotterdam"
                />
                <mrd-field-error
                  [control]="form.controls.name"
                  label="Name"
                  [submitted]="submitted()"
                />
              </div>

              <div class="field">
                <label for="rule-new-scope">Scope</label>
                <select id="rule-new-scope" formControlName="scope">
                  @for (option of scopes; track option.value) {
                    <option [value]="option.value">{{ option.label }}</option>
                  }
                </select>
                <p class="field__hint">The record the expression is handed when it runs.</p>
              </div>
            </div>

            <div class="field">
              <label for="rule-expression">Expression</label>
              <textarea
                id="rule-expression"
                rows="3"
                formControlName="expression"
                placeholder="consignment.weightKg > 12000"
              ></textarea>
              <mrd-field-error
                [control]="form.controls.expression"
                label="Expression"
                [submitted]="submitted()"
              />
            </div>

            <label class="toggle">
              <input type="checkbox" formControlName="enabled" />
              <span>Run this rule straight away</span>
            </label>

            <div class="row">
              <button type="submit" class="btn btn--primary" [disabled]="busy()">
                {{ busy() ? 'Saving…' : 'Save rule' }}
              </button>
              <button type="button" class="btn btn--ghost" (click)="toggleCreate()">Discard</button>
              @if (created(); as record) {
                <span class="muted small">Saved “{{ record.name }}”.</span>
              }
            </div>
          </form>
        </section>
      }

      <mrd-filter-bar (reset)="clear()">
        <div class="field">
          <label for="rule-scope">Scope</label>
          <select id="rule-scope" [formControl]="scopeFilter">
            <option value="">All scopes</option>
            @for (option of scopes; track option.value) {
              <option [value]="option.value">{{ option.label }}</option>
            }
          </select>
        </div>
      </mrd-filter-bar>

      <div class="card">
        <div class="card__header">
          <h2>Rule book</h2>
          <span class="muted small">{{ countLabel() }}</span>
        </div>
        <mrd-data-table
          [columns]="columns"
          [rows]="visible()"
          [loading]="loading()"
          [link]="ruleLink"
          emptyTitle="No rules in this scope"
          emptyMessage="Nothing has been written for this scope yet. Clear the filter to see the whole rule book."
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
      .create {
        margin-bottom: 16px;
      }

      .toggle {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 14px;
        font-size: 13px;
      }

      .toggle input {
        width: auto;
      }
    `,
  ],
})
export class RulesListComponent {
  private readonly api = inject(RulesApi);
  private readonly fb = inject(FormBuilder);

  readonly scopes = SCOPES;
  readonly scopeFilter = new FormControl<RuleScope | ''>('', { nonNullable: true });
  readonly scope = signal<RuleScope | ''>('');

  readonly page = signal<Page<RuleSummary>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly creating = signal(false);
  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly created = signal<RuleSummary | null>(null);

  readonly form = this.fb.nonNullable.group({
    name: ['', [Validators.required, Validators.maxLength(80)]],
    scope: ['consignment' as RuleScope, [Validators.required]],
    expression: ['', [Validators.required, Validators.minLength(3)]],
    enabled: [true],
  });

  /**
   * The rule book is small enough that the API answers with the whole book, so the
   * scope filter is applied here rather than costing another round trip.
   */
  readonly visible = computed(() => {
    const scope = this.scope();
    const items = this.page().items;
    return scope === '' ? items : items.filter((rule) => rule.scope === scope);
  });

  readonly countLabel = computed(() => {
    const shown = this.visible().length;
    const total = this.page().items.length;
    return shown === total ? `${total} rules` : `${shown} of ${total} rules`;
  });

  readonly columns: TableColumn<RuleSummary>[] = [
    { key: 'name', label: 'Rule' },
    { key: 'scope', label: 'Scope', width: '130px' },
    {
      key: 'expression',
      label: 'Expression',
      mono: true,
      value: (row) => shorten(row.expression),
    },
    {
      key: 'enabled',
      label: 'State',
      pill: true,
      width: '110px',
      value: (row) => (row.enabled ? 'enabled' : 'disabled'),
    },
    { key: 'updatedAt', label: 'Updated', width: '170px', value: (row) => stamp(row.updatedAt) },
    { key: 'updatedBy', label: 'Updated by', width: '150px' },
    { key: 'matchCount', label: 'Matches', align: 'end', width: '100px' },
  ];

  constructor() {
    this.scopeFilter.valueChanges.subscribe((value) => this.scope.set(value));
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
    // The endpoint returns the whole rule book in one page today; the pager is wired so
    // the screen behaves like every other list once the book outgrows a single page.
    this.page.update((current) => ({ ...current, page }));
    this.load();
  }

  clear(): void {
    this.scopeFilter.setValue('');
  }

  toggleCreate(): void {
    const next = !this.creating();
    this.creating.set(next);
    if (next) {
      this.created.set(null);
      this.submitted.set(false);
      this.form.reset({ name: '', scope: 'consignment', expression: '', enabled: true });
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
      next: (rule) => {
        this.busy.set(false);
        this.created.set(rule);
        this.submitted.set(false);
        this.form.reset({ name: '', scope: rule.scope, expression: '', enabled: true });
        // Show the new rule immediately rather than making the operator reload the book.
        this.page.update((current) => ({
          ...current,
          items: [rule, ...current.items],
          total: current.total + 1,
        }));
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  ruleLink = (row: RuleSummary): unknown[] => ['/rules', row.id];
}
