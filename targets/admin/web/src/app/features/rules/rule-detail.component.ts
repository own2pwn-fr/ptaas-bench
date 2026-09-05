import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, effect, inject, input, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { RulesApi } from '../../core/api/rules.api';
import { ApiError } from '../../core/models/api-error.model';
import { RuleSummary } from '../../core/models/domain.model';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';

/** One rule: what it reads, what it says, and how often it has matched. */
@Component({
  selector: 'mrd-rule-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    EmptyStateComponent,
    ErrorBannerComponent,
    PageHeaderComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        [title]="rule()?.name ?? 'Rule'"
        [subtitle]="rule() ? rule()!.scope + ' rule · last changed by ' + rule()!.updatedBy : 'Loading…'"
      >
        <a actions class="btn btn--sm" routerLink="/rules">Back to rules</a>
        <button
          actions
          type="button"
          class="btn btn--sm btn--primary"
          [disabled]="rule() === null"
          (click)="openPreview()"
        >
          Try this expression
        </button>
      </mrd-page-header>

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      @if (loading()) {
        <div class="card" style="padding: 16px">
          <mrd-skeleton [rows]="6" />
        </div>
      } @else if (rule()) {
        @let record = rule()!;
        <div class="grid grid--2">
          <section class="card">
            <div class="card__header"><h2>Rule</h2></div>
            <div class="card__body">
              <dl class="detail">
                <dt>Name</dt>
                <dd>{{ record.name }}</dd>
                <dt>Scope</dt>
                <dd>{{ record.scope }}</dd>
                <dt>State</dt>
                <dd><mrd-status-pill [status]="record.enabled ? 'enabled' : 'disabled'" /></dd>
                <dt>Last change</dt>
                <dd>{{ record.updatedAt | date: 'd MMM y, HH:mm' }}</dd>
                <dt>Changed by</dt>
                <dd>{{ record.updatedBy }}</dd>
                <dt>Last matched</dt>
                <dd>
                  @if (record.lastMatchedAt) {
                    {{ record.lastMatchedAt | date: 'd MMM y, HH:mm' }}
                  } @else {
                    <span class="muted">Has not matched a record yet</span>
                  }
                </dd>
                <dt>Matches</dt>
                <dd>{{ (record.matchCount ?? 0) | number }}</dd>
              </dl>
            </div>
          </section>

          <section class="card">
            <div class="card__header">
              <h2>Expression</h2>
              <span class="muted small">Read-only here; changes are made in the rule editor</span>
            </div>
            <div class="card__body">
              <pre class="payload">{{ record.expression }}</pre>
              <p class="field__hint expression-note">
                The engine hands the rule the {{ record.scope }} record being processed and keeps
                the outcome on the audit trail.
              </p>
            </div>
          </section>
        </div>
      } @else {
        <mrd-empty-state
          title="Rule unavailable"
          message="This rule is no longer in the book. It may have been retired at quarter end."
        />
      }
    </div>
  `,
  styles: [
    `
      .expression-note {
        margin: 12px 0 0;
      }
    `,
  ],
})
export class RuleDetailComponent {
  /** Bound from the `:id` path segment by the router's component input binding. */
  readonly id = input.required<string>();

  private readonly api = inject(RulesApi);
  private readonly router = inject(Router);

  readonly rule = signal<RuleSummary | null>(null);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  constructor() {
    effect(() => {
      if (this.id()) {
        this.load();
      }
    });
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.get(this.id()).subscribe({
      next: (rule) => {
        this.rule.set(rule);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.rule.set(null);
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  /** Hand the expression to the preview screen so it can be run against a record. */
  openPreview(): void {
    const record = this.rule();
    if (record === null) {
      return;
    }
    void this.router.navigate(['/rules', 'preview'], {
      queryParams: { expression: record.expression },
    });
  }
}
