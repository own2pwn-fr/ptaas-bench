import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { ReportsApi } from '../../core/api/reports.api';
import { ApiError } from '../../core/models/api-error.model';
import { SummaryReport } from '../../core/models/domain.model';
import { SupportDeskService } from '../../core/services/support-desk.service';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  SkeletonComponent,
} from '../../shared';
import { ReportTabsComponent } from './report-tabs.component';

/** The headline figures for one account, as read out on an account review. */
@Component({
  selector: 'mrd-summary-report',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    EmptyStateComponent,
    ErrorBannerComponent,
    PageHeaderComponent,
    ReportTabsComponent,
    SkeletonComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Account summary"
        subtitle="Consignments, invoiced value and the busiest lanes"
      >
        <button actions type="button" class="btn btn--sm" (click)="load()" [disabled]="loading()">
          {{ loading() ? 'Compiling…' : 'Recompile' }}
        </button>
        <a actions class="btn btn--sm btn--primary" routerLink="/reports/volumes">Volumes</a>
      </mrd-page-header>

      <mrd-report-tabs />

      <mrd-error-banner
        [error]="failure()"
        heading="The summary could not be compiled"
        (retry)="load()"
        (dismiss)="failure.set(null)"
      />

      @if (desk.actingForOtherAccount()) {
        <p class="desk-note small">
          Compiled for {{ desk.selectedAccountName() }}, chosen in the desk switcher. Switch back
          to your own account in the top bar to see your usual figures.
        </p>
      }

      @if (loading()) {
        <div class="card card--pad">
          <mrd-skeleton [rows]="5" />
        </div>
      } @else if (report()) {
        @let summary = report()!;
        <section class="grid grid--4 tiles">
          @for (tile of summary.tiles; track tile.key) {
            <article class="card metric">
              <div class="metric__label">{{ tile.label }}</div>
              <div class="metric__value">
                {{ tile.value | number: '1.0-2' }}
                @if (tile.unit) {
                  <span class="metric__unit">{{ tile.unit }}</span>
                }
              </div>
              @if (tile.delta !== undefined) {
                <div class="metric__trend" [class.metric__trend--down]="tile.delta < 0">
                  {{ tile.delta > 0 ? '▲' : '▼' }} {{ tile.delta | number: '1.0-1' }}% on the
                  previous period
                </div>
              }
            </article>
          }
        </section>

        <div class="card">
          <div class="card__header">
            <h2>Busiest lanes</h2>
            <span class="small muted">By consignments moved in the period</span>
          </div>
          <div class="card__body">
            @if (summary.topLanes.length === 0) {
              <mrd-empty-state
                title="No lane activity"
                message="Nothing moved on this account in the period covered by the summary."
              />
            } @else {
              <table class="data">
                <thead>
                  <tr>
                    <th scope="col">Lane</th>
                    <th scope="col" class="numeric">Consignments</th>
                    <th scope="col" class="numeric">Gross weight</th>
                  </tr>
                </thead>
                <tbody>
                  @for (lane of summary.topLanes; track lane.lane) {
                    <tr>
                      <td>{{ lane.lane }}</td>
                      <td class="numeric">{{ lane.consignments | number }}</td>
                      <td class="numeric">{{ lane.grossWeightKg | number: '1.0-0' }} kg</td>
                    </tr>
                  }
                </tbody>
              </table>
            }
          </div>
          <div class="card__footer small muted">
            Compiled for {{ summary.accountName }}
            (<span class="mono">{{ summary.accountId }}</span
            >) on {{ summary.generatedAt | date: 'd MMM y, HH:mm' }}
          </div>
        </div>
      } @else {
        <mrd-empty-state
          title="No summary available"
          message="Reporting has not published a summary for this account yet. It is produced once the first consignment is settled."
        />
      }
    </div>
  `,
  styles: [
    `
      .card--pad {
        padding: 16px;
      }

      .tiles {
        margin-bottom: 16px;
      }

      .metric__unit {
        font-size: 14px;
        font-weight: 500;
        color: var(--mrd-ink-faint);
        margin-left: 4px;
      }

      .metric__trend--down {
        color: var(--mrd-danger);
      }

      .desk-note {
        padding: 8px 12px;
        margin-bottom: 16px;
        background: var(--mrd-accent-soft);
        border: 1px solid var(--mrd-line);
        border-radius: var(--mrd-radius-sm);
        color: var(--mrd-accent-ink);
      }
    `,
  ],
})
export class SummaryReportComponent {
  private readonly api = inject(ReportsApi);

  readonly desk = inject(SupportDeskService);

  readonly report = signal<SummaryReport | null>(null);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.summary().subscribe({
      next: (summary) => {
        this.report.set(summary);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }
}
