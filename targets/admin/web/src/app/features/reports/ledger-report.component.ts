import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';

import { toApiError } from '../../core/api/api-error.util';
import { ReportsApi } from '../../core/api/reports.api';
import { ApiError } from '../../core/models/api-error.model';
import { LedgerReport, LedgerRow } from '../../core/models/domain.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FilterBarComponent,
  PageHeaderComponent,
  TableColumn,
} from '../../shared';
import { ReportTabsComponent } from './report-tabs.component';

interface WindowPreset {
  value: string;
  label: string;
}

/** The periods finance ask for most; anything else is typed into the custom field. */
const WINDOW_PRESETS: WindowPreset[] = [
  { value: 'last-7-days', label: 'Last 7 days' },
  { value: 'last-30-days', label: 'Last 30 days' },
  { value: 'last-quarter', label: 'Last quarter' },
  { value: 'year-to-date', label: 'Year to date' },
];

const DEFAULT_WINDOW = 'last-30-days';

/** Postings for a period, with the balance carried in and out. */
@Component({
  selector: 'mrd-ledger-report',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    DataTableComponent,
    ErrorBannerComponent,
    FilterBarComponent,
    PageHeaderComponent,
    ReportTabsComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Ledger"
        subtitle="Postings, opening balance and closing balance for a period"
      />

      <mrd-report-tabs />

      <mrd-error-banner
        [error]="failure()"
        heading="The ledger could not be compiled"
        (retry)="load()"
        (dismiss)="failure.set(null)"
      />

      <form [formGroup]="form" (ngSubmit)="load()" novalidate>
        <mrd-filter-bar (reset)="clear()">
          <div class="field">
            <label for="ledger-preset">Period</label>
            <select
              id="ledger-preset"
              [value]="form.controls.window.value"
              (change)="applyPreset($any($event.target).value)"
            >
              @for (preset of presets; track preset.value) {
                <option [value]="preset.value">{{ preset.label }}</option>
              }
            </select>
          </div>

          <div class="field field--wide">
            <label for="ledger-window">Custom period</label>
            <input
              id="ledger-window"
              type="text"
              formControlName="window"
              placeholder="2026-Q1 or 2026-04-01..2026-06-30"
            />
            <p class="field__hint">
              Overwrites the preset. Finance regularly ask for a period the presets do not
              cover, such as a single customs quarter.
            </p>
          </div>

          <div class="field">
            <label for="ledger-account">Account</label>
            <input
              id="ledger-account"
              type="search"
              formControlName="account"
              placeholder="CW-40118 (optional)"
            />
            <p class="field__hint">Leave empty for every account on the desk.</p>
          </div>

          <button extraActions type="submit" class="btn btn--sm btn--primary" [disabled]="loading()">
            {{ loading() ? 'Compiling…' : 'Run ledger' }}
          </button>
        </mrd-filter-bar>
      </form>

      @if (report(); as ledger) {
        <section class="grid grid--3 balances">
          <article class="card metric">
            <div class="metric__label">Opening balance</div>
            <div class="metric__value">{{ money(ledger.openingBalance, ledger.currency) }}</div>
            <div class="metric__trend">Carried in from the previous period</div>
          </article>
          <article class="card metric">
            <div class="metric__label">Movement</div>
            <div class="metric__value">
              {{ money(ledger.closingBalance - ledger.openingBalance, ledger.currency) }}
            </div>
            <div class="metric__trend">{{ ledger.rows.length }} postings in this period</div>
          </article>
          <article class="card metric">
            <div class="metric__label">Closing balance</div>
            <div class="metric__value">{{ money(ledger.closingBalance, ledger.currency) }}</div>
            <div class="metric__trend">Carried out to the next period</div>
          </article>
        </section>
      }

      <div class="card">
        <div class="card__header">
          <h2>Postings</h2>
          <span class="small muted">Debits and credits as posted, newest first</span>
        </div>

        <mrd-data-table
          [columns]="columns"
          [rows]="report()?.rows ?? []"
          [loading]="loading()"
          emptyTitle="No postings in this period"
          emptyMessage="Nothing was posted for the period requested. Widen the period or check the account reference."
        />

        <div class="card__footer small muted">
          @if (report(); as ledger) {
            Window applied: <span class="mono">{{ ledger.window }}</span> ·
            {{ ledger.account ? 'Account ' + ledger.account : 'All accounts on the desk' }} ·
            Amounts in {{ ledger.currency }}
          } @else {
            Choose a period and run the ledger.
          }
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .field--wide {
        min-width: 260px;
      }

      .balances {
        margin-bottom: 16px;
      }
    `,
  ],
})
export class LedgerReportComponent {
  private readonly api = inject(ReportsApi);
  private readonly fb = inject(FormBuilder);

  readonly presets = WINDOW_PRESETS;

  /**
   * The preset picker and the free-text field write to the same `window` control: the
   * API takes one period expression, whichever way the operator arrived at it.
   */
  readonly form = this.fb.nonNullable.group({
    window: [DEFAULT_WINDOW],
    account: [''],
  });

  readonly report = signal<LedgerReport | null>(null);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly columns: TableColumn<LedgerRow>[] = [
    { key: 'postedOn', label: 'Posted', width: '110px' },
    { key: 'document', label: 'Document', mono: true, width: '150px' },
    { key: 'accountName', label: 'Account', width: '200px' },
    { key: 'narrative', label: 'Narrative' },
    {
      key: 'debit',
      label: 'Debit',
      align: 'end',
      width: '130px',
      value: (row) => (row.debit === 0 ? '' : amount(row.debit)),
    },
    {
      key: 'credit',
      label: 'Credit',
      align: 'end',
      width: '130px',
      value: (row) => (row.credit === 0 ? '' : amount(row.credit)),
    },
    {
      key: 'balance',
      label: 'Balance',
      align: 'end',
      width: '140px',
      value: (row) => amount(row.balance),
    },
  ];

  constructor() {
    this.load();
  }

  applyPreset(value: string): void {
    this.form.controls.window.setValue(value);
    this.load();
  }

  load(): void {
    const { window, account } = this.form.getRawValue();

    this.loading.set(true);
    this.failure.set(null);
    this.api.ledger(window.trim(), account.trim() || null).subscribe({
      next: (result) => {
        this.report.set(result);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  clear(): void {
    this.form.reset({ window: DEFAULT_WINDOW, account: '' });
    this.load();
  }

  money(value: number, currency: string): string {
    return `${amount(value)} ${currency}`;
  }
}

/** Two decimals and thousands separators, the way the statements are printed. */
function amount(value: number): string {
  return value.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
