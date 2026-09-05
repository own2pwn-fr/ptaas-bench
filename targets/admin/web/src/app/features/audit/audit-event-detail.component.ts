import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { AuditApi } from '../../core/api/audit.api';
import { ApiError } from '../../core/models/api-error.model';
import { AuditEvent } from '../../core/models/domain.model';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';

/**
 * One audit event, in full.
 *
 * This is the screen that gets printed or pasted into a claim, so every field the API
 * holds is shown as it was stored — including the raw attributes, which vary by action.
 */
@Component({
  selector: 'mrd-audit-event-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
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
        [title]="event()?.action ?? 'Audit event'"
        [subtitle]="event() ? 'Recorded ' + (event()!.at | date: 'd MMM y, HH:mm:ss') : 'Loading…'"
      >
        <a actions class="btn btn--sm" routerLink="/audit">Back to the trail</a>
      </mrd-page-header>

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      @if (loading()) {
        <div class="card card--pad">
          <mrd-skeleton [rows]="8" />
        </div>
      } @else {
        @if (event(); as record) {
          <div class="stack">
            <section class="card">
              <div class="card__header"><h2>Event</h2></div>
              <div class="card__body">
                <dl class="detail">
                  <dt>Event id</dt>
                  <dd class="mono">{{ record.id }}</dd>
                  <dt>Recorded</dt>
                  <dd>{{ record.at | date: 'd MMM y, HH:mm:ss' }}</dd>
                  <dt>Action</dt>
                  <dd class="mono">{{ record.action }}</dd>
                  <dt>Target</dt>
                  <dd>{{ record.target }}</dd>
                  <dt>Outcome</dt>
                  <dd><mrd-status-pill [status]="record.outcome" /></dd>
                  <dt>Actor</dt>
                  <dd>{{ record.actor }}</dd>
                  <dt>Actor id</dt>
                  <dd class="mono">{{ record.actorId }}</dd>
                  <dt>Source address</dt>
                  <dd class="mono">{{ record.sourceAddress }}</dd>
                  <dt>User agent</dt>
                  <dd class="mono agent">{{ record.userAgent || 'Not recorded' }}</dd>
                </dl>
              </div>
            </section>

            @if (record.actorDetail; as person) {
              <section class="card">
                <div class="card__header"><h2>Actor record</h2></div>
                <div class="card__body">
                  <dl class="detail">
                    <dt>Name</dt>
                    <dd>{{ person.displayName }}</dd>
                    <dt>Job title</dt>
                    <dd>{{ person.jobTitle }}</dd>
                    <dt>Department</dt>
                    <dd>{{ person.department }}</dd>
                    <dt>Office</dt>
                    <dd>{{ person.office }}</dd>
                    <dt>Email</dt>
                    <dd><a href="mailto:{{ person.email }}">{{ person.email }}</a></dd>
                  </dl>
                </div>
              </section>
            }

            <section class="card">
              <div class="card__header">
                <h2>Attributes</h2>
                <span class="muted small">As written by the service that raised the event</span>
              </div>
              <div class="card__body">
                @if (attributes() === null) {
                  <p class="muted small">This action was recorded without extra attributes.</p>
                } @else {
                  <pre class="payload">{{ attributes() }}</pre>
                }
              </div>
            </section>
          </div>
        } @else {
          <div class="card">
            <mrd-empty-state
              title="No such event"
              message="Nothing in the trail carries this reference. It may have aged out of the retention window."
            >
              <a class="btn btn--sm btn--primary" routerLink="/audit">Back to the trail</a>
            </mrd-empty-state>
          </div>
        }
      }
    </div>
  `,
  styles: [
    `
      .card--pad {
        padding: 16px;
      }

      .agent {
        word-break: break-word;
      }
    `,
  ],
})
export class AuditEventDetailComponent {
  /** Bound from the `:id` path segment by the router's component input binding. */
  readonly id = input.required<string>();

  private readonly api = inject(AuditApi);

  readonly event = signal<AuditEvent | null>(null);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  /** Pretty-printed attributes, or null when the event carries none. */
  readonly attributes = computed<string | null>(() => {
    const source = this.event()?.attributes;
    if (!source || Object.keys(source).length === 0) {
      return null;
    }
    return JSON.stringify(source, null, 2);
  });

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
    this.api.event(this.id()).subscribe({
      next: (record) => {
        this.event.set(record);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.event.set(null);
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }
}
