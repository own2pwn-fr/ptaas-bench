import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';

import { toApiError } from '../../core/api/api-error.util';
import { ReportsApi } from '../../core/api/reports.api';
import { ApiError } from '../../core/models/api-error.model';
import { VolumePoint, VolumeReport } from '../../core/models/domain.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  FilterBarComponent,
  PageHeaderComponent,
  TableColumn,
} from '../../shared';
import { ReportTabsComponent } from './report-tabs.component';

interface GranularityChoice {
  value: string;
  label: string;
}

const GRANULARITIES: GranularityChoice[] = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
];

/** Consignments, TEU and chargeable weight over a period. */
@Component({
  selector: 'mrd-volumes-report',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DecimalPipe,
    ReactiveFormsModule,
    DataTableComponent,
    ErrorBannerComponent,
    FieldErrorComponent,
    FilterBarComponent,
    PageHeaderComponent,
    ReportTabsComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Volumes"
        subtitle="What moved, bucketed by day, week or month"
      />

      <mrd-report-tabs />

      <mrd-error-banner
        [error]="failure()"
        heading="The volumes report could not be compiled"
        (retry)="load()"
        (dismiss)="failure.set(null)"
      />

      <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
        <mrd-filter-bar (reset)="clear()">
          <div class="field">
            <label for="volumes-from">From</label>
            <input id="volumes-from" type="date" formControlName="from" />
            <mrd-field-error
              [control]="form.controls.from"
              label="From"
              [submitted]="submitted()"
            />
          </div>

          <div class="field">
            <label for="volumes-to">To</label>
            <input id="volumes-to" type="date" formControlName="to" />
            <mrd-field-error [control]="form.controls.to" label="To" [submitted]="submitted()" />
          </div>

          <div class="field">
            <label for="volumes-granularity">Bucket</label>
            <select id="volumes-granularity" formControlName="granularity">
              @for (choice of granularities; track choice.value) {
                <option [value]="choice.value">{{ choice.label }}</option>
              }
            </select>
            <p class="field__hint">Weeks start on Monday, as the carrier schedules do.</p>
          </div>

          <button extraActions type="submit" class="btn btn--sm btn--primary" [disabled]="loading()">
            {{ loading() ? 'Compiling…' : 'Run report' }}
          </button>
        </mrd-filter-bar>
      </form>

      <div class="card chart-card">
        <div class="card__header">
          <h2>Consignments per {{ bucketLabel() }}</h2>
          <span class="small muted">Peak {{ peak() | number }} in a single bucket</span>
        </div>
        <div class="card__body">
          @if (loading()) {
            <p class="muted small">Compiling the period…</p>
          } @else if (points().length === 0) {
            <p class="muted small">
              Nothing moved in this period, so there is nothing to plot. Widen the dates or
              switch the bucket to month.
            </p>
          } @else {
            <div class="bars" role="img" [attr.aria-label]="chartLabel()">
              @for (point of points(); track point.bucket) {
                <div class="bars__slot" [title]="tooltip(point)">
                  <span class="bars__value small">{{ point.consignments | number }}</span>
                  <div class="bars__track">
                    <div class="bars__bar" [style.height.%]="share(point)"></div>
                  </div>
                  <span class="bars__label small muted">{{ point.bucket }}</span>
                </div>
              }
            </div>
          }
        </div>
      </div>

      <div class="card">
        <div class="card__header">
          <h2>Breakdown</h2>
          @if (report(); as volumes) {
            <span class="small muted">
              {{ volumes.from }} to {{ volumes.to }}, by {{ volumes.granularity }}
            </span>
          }
        </div>
        <mrd-data-table
          [columns]="columns"
          [rows]="points()"
          [loading]="loading()"
          [trackBy]="'bucket'"
          emptyTitle="No volume in this period"
          emptyMessage="No consignment was booked between the dates requested."
        />
        <div class="card__footer small muted">
          Totals: {{ totalConsignments() | number }} consignments ·
          {{ totalTeu() | number: '1.0-1' }} TEU ·
          {{ totalWeight() | number: '1.0-0' }} kg chargeable
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .chart-card {
        margin-bottom: 16px;
      }

      .bars {
        display: flex;
        align-items: flex-end;
        gap: 8px;
        overflow-x: auto;
        padding-bottom: 4px;
      }

      .bars__slot {
        display: flex;
        flex: 1 1 32px;
        min-width: 44px;
        flex-direction: column;
        align-items: center;
        gap: 4px;
      }

      .bars__track {
        display: flex;
        align-items: flex-end;
        width: 100%;
        height: 140px;
        background: var(--mrd-surface-sunken);
        border-radius: var(--mrd-radius-sm);
      }

      .bars__bar {
        width: 100%;
        min-height: 2px;
        background: var(--mrd-accent);
        border-radius: var(--mrd-radius-sm);
      }

      .bars__value {
        font-variant-numeric: tabular-nums;
        font-weight: 600;
      }

      .bars__label {
        white-space: nowrap;
        font-size: 11px;
      }
    `,
  ],
})
export class VolumesReportComponent {
  private readonly api = inject(ReportsApi);
  private readonly fb = inject(FormBuilder);

  readonly granularities = GRANULARITIES;

  readonly form = this.fb.nonNullable.group({
    from: [startOfMonth(), [Validators.required]],
    to: [today(), [Validators.required]],
    granularity: ['day', [Validators.required]],
  });

  readonly report = signal<VolumeReport | null>(null);
  readonly loading = signal(true);
  readonly submitted = signal(false);
  readonly failure = signal<ApiError | null>(null);

  readonly points = computed<VolumePoint[]>(() => this.report()?.points ?? []);

  /** Tallest bucket in the period; the bars are drawn as a share of it. */
  readonly peak = computed(() =>
    this.points().reduce((highest, point) => Math.max(highest, point.consignments), 0),
  );

  readonly totalConsignments = computed(() =>
    this.points().reduce((sum, point) => sum + point.consignments, 0),
  );

  readonly totalTeu = computed(() => this.points().reduce((sum, point) => sum + point.teu, 0));

  readonly totalWeight = computed(() =>
    this.points().reduce((sum, point) => sum + point.chargeableWeightKg, 0),
  );

  readonly bucketLabel = computed(() => this.report()?.granularity ?? 'bucket');

  readonly chartLabel = computed(
    () => `Consignments per ${this.bucketLabel()} across ${this.points().length} buckets`,
  );

  readonly columns: TableColumn<VolumePoint>[] = [
    { key: 'bucket', label: 'Bucket', mono: true, width: '150px' },
    {
      key: 'consignments',
      label: 'Consignments',
      align: 'end',
      width: '150px',
      value: (row) => row.consignments.toLocaleString('en-GB'),
    },
    {
      key: 'teu',
      label: 'TEU',
      align: 'end',
      width: '120px',
      value: (row) => row.teu.toLocaleString('en-GB', { maximumFractionDigits: 1 }),
    },
    {
      key: 'chargeableWeightKg',
      label: 'Chargeable weight',
      align: 'end',
      width: '180px',
      value: (row) => `${row.chargeableWeightKg.toLocaleString('en-GB')} kg`,
    },
  ];

  constructor() {
    this.load();
  }

  submit(): void {
    this.submitted.set(true);
    if (this.form.invalid || this.loading()) {
      return;
    }
    this.load();
  }

  load(): void {
    const { from, to, granularity } = this.form.getRawValue();

    this.loading.set(true);
    this.failure.set(null);
    this.api.volumes(from, to, granularity).subscribe({
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
    this.submitted.set(false);
    this.form.reset({ from: startOfMonth(), to: today(), granularity: 'day' });
    this.load();
  }

  share(point: VolumePoint): number {
    const peak = this.peak();
    return peak === 0 ? 0 : Math.round((point.consignments / peak) * 100);
  }

  tooltip(point: VolumePoint): string {
    return (
      `${point.bucket}: ${point.consignments.toLocaleString('en-GB')} consignments, ` +
      `${point.teu.toLocaleString('en-GB', { maximumFractionDigits: 1 })} TEU, ` +
      `${point.chargeableWeightKg.toLocaleString('en-GB')} kg chargeable`
    );
  }
}

/** First day of the current month — the period operations look at by default. */
function startOfMonth(): string {
  const now = new Date();
  return isoDate(new Date(now.getFullYear(), now.getMonth(), 1));
}

function today(): string {
  return isoDate(new Date());
}

/** Local calendar date, not UTC: a booking made late in the evening belongs to that day. */
function isoDate(value: Date): string {
  const month = `${value.getMonth() + 1}`.padStart(2, '0');
  const day = `${value.getDate()}`.padStart(2, '0');
  return `${value.getFullYear()}-${month}-${day}`;
}
