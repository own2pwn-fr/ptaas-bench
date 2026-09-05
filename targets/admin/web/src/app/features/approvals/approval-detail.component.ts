import { DatePipe, DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { ApprovalsApi } from '../../core/api/approvals.api';
import { ApiError } from '../../core/models/api-error.model';
import { Approval } from '../../core/models/domain.model';
import { SessionService } from '../../core/services/session.service';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';

const KINDS: Readonly<Record<Approval['kind'], string>> = {
  'credit-limit': 'Credit limit',
  'rate-override': 'Rate override',
  'write-off': 'Write-off',
  'account-opening': 'Account opening',
};

/**
 * One approval request, with the decision panel for whoever is on the commercial desk.
 *
 * Viewers can read the register but not act on it, so the panel is only rendered for
 * analysts and above; the API applies the same rule when the decision is posted.
 */
@Component({
  selector: 'mrd-approval-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    DecimalPipe,
    ReactiveFormsModule,
    RouterLink,
    EmptyStateComponent,
    ErrorBannerComponent,
    FieldErrorComponent,
    PageHeaderComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        [title]="approval()?.subject ?? 'Approval request'"
        [subtitle]="approval() ? approval()!.reference + ' · raised by ' + approval()!.raisedBy : 'Loading…'"
      >
        <a actions class="btn btn--sm" routerLink="/approvals">Back to the register</a>
      </mrd-page-header>

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      @if (loading()) {
        <div class="card" style="padding: 16px">
          <mrd-skeleton [rows]="6" />
        </div>
      } @else if (approval()) {
        @let record = approval()!;
        <div class="grid grid--2">
          <section class="card">
            <div class="card__header">
              <h2>Request</h2>
              <mrd-status-pill [status]="record.state" />
            </div>
            <div class="card__body">
              <dl class="detail">
                <dt>Reference</dt>
                <dd class="mono">{{ record.reference }}</dd>
                <dt>Subject</dt>
                <dd>{{ record.subject }}</dd>
                <dt>Kind</dt>
                <dd>{{ kindLabel(record) }}</dd>
                <dt>Amount</dt>
                <dd>
                  @if (record.amount !== undefined) {
                    {{ record.amount | number: '1.2-2' }} {{ record.currency ?? '' }}
                  } @else {
                    <span class="muted">No amount attached</span>
                  }
                </dd>
                <dt>Account</dt>
                <dd>
                  @if (record.accountId) {
                    <a [routerLink]="['/orgs', record.accountId]">
                      {{ record.accountName ?? record.accountId }}
                    </a>
                  } @else {
                    <span class="muted">Not tied to an account</span>
                  }
                </dd>
                <dt>Raised by</dt>
                <dd>{{ record.raisedBy }}</dd>
                <dt>Raised</dt>
                <dd>{{ record.raisedAt | date: 'd MMM y, HH:mm' }}</dd>
                @if (record.decidedAt) {
                  <dt>Decided by</dt>
                  <dd>{{ record.decidedBy ?? '—' }}</dd>
                  <dt>Decided</dt>
                  <dd>{{ record.decidedAt | date: 'd MMM y, HH:mm' }}</dd>
                }
              </dl>
            </div>
          </section>

          @if (record.state === 'pending') {
            @if (canDecide()) {
              <section class="card">
                <div class="card__header">
                  <h2>Decision</h2>
                  <span class="muted small">Signed as {{ session.displayName() }}</span>
                </div>
                <form class="card__body" [formGroup]="form" novalidate>
                  <div class="field">
                    <label for="decision-note">Note</label>
                    <textarea
                      id="decision-note"
                      rows="5"
                      formControlName="note"
                      placeholder="Cover checked against the last four quarters; limit raise agreed with the branch."
                    ></textarea>
                    <p class="field__hint">
                      The note is kept on the audit trail and is read at quarter end, so say why
                      the request was settled the way it was.
                    </p>
                    <mrd-field-error
                      [control]="form.controls.note"
                      label="Note"
                      [submitted]="submitted()"
                    />
                  </div>

                  <div class="row row--wrap">
                    <button
                      type="button"
                      class="btn btn--primary"
                      [disabled]="busy()"
                      (click)="decide('approve')"
                    >
                      {{ busy() ? 'Working…' : 'Approve' }}
                    </button>
                    <button
                      type="button"
                      class="btn btn--danger"
                      [disabled]="busy()"
                      (click)="decide('reject')"
                    >
                      Reject
                    </button>
                  </div>
                </form>
              </section>
            } @else {
              <section class="card">
                <div class="card__header"><h2>Decision</h2></div>
                <div class="card__body">
                  <mrd-empty-state
                    title="Waiting on the commercial desk"
                    message="Your role can follow this request but not settle it. Ask the desk on extension 4120 if it is holding a shipment."
                  />
                </div>
              </section>
            }
          } @else {
            <section class="card">
              <div class="card__header">
                <h2>Decision</h2>
                <mrd-status-pill [status]="record.state" />
              </div>
              <div class="card__body">
                <dl class="detail">
                  <dt>Outcome</dt>
                  <dd>{{ outcomeLabel(record) }}</dd>
                  <dt>Decided by</dt>
                  <dd>{{ record.decidedBy ?? 'Not recorded' }}</dd>
                  <dt>Decided</dt>
                  <dd>
                    @if (record.decidedAt) {
                      {{ record.decidedAt | date: 'd MMM y, HH:mm' }}
                    } @else {
                      <span class="muted">Not recorded</span>
                    }
                  </dd>
                </dl>
                <h3 class="note-heading">Note</h3>
                @if (record.note) {
                  <p class="note">{{ record.note }}</p>
                } @else {
                  <p class="muted small">No note was left with the decision.</p>
                }
              </div>
            </section>
          }
        </div>

        @if (confirmation(); as message) {
          <p class="small confirmation">{{ message }}</p>
        }
      } @else {
        <mrd-empty-state
          title="Request unavailable"
          message="This request is no longer on the register. It may have been withdrawn by the branch that raised it."
        />
      }
    </div>
  `,
  styles: [
    `
      .note-heading {
        margin: 16px 0 6px;
      }

      .note {
        margin: 0;
        padding: 10px 12px;
        background: var(--mrd-surface-sunken);
        border-radius: var(--mrd-radius-sm);
      }

      .confirmation {
        margin-top: 16px;
        color: var(--mrd-good);
        font-weight: 600;
      }
    `,
  ],
})
export class ApprovalDetailComponent {
  /** Bound from the `:id` path segment by the router's component input binding. */
  readonly id = input.required<string>();

  private readonly api = inject(ApprovalsApi);
  private readonly fb = inject(FormBuilder);

  readonly session = inject(SessionService);

  readonly approval = signal<Approval | null>(null);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);
  readonly submitted = signal(false);
  readonly busy = signal(false);
  readonly confirmation = signal('');

  readonly canDecide = computed(() => this.session.hasRole('analyst'));

  readonly form = this.fb.nonNullable.group({
    note: ['', [Validators.required, Validators.minLength(10), Validators.maxLength(600)]],
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
    this.api.get(this.id()).subscribe({
      next: (approval) => {
        this.approval.set(approval);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.approval.set(null);
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  kindLabel(record: Approval): string {
    return KINDS[record.kind] ?? record.kind;
  }

  outcomeLabel(record: Approval): string {
    switch (record.state) {
      case 'approved':
        return 'Approved';
      case 'rejected':
        return 'Rejected';
      case 'withdrawn':
        return 'Withdrawn by the branch that raised it';
      default:
        return 'Waiting on a decision';
    }
  }

  decide(decision: 'approve' | 'reject'): void {
    this.submitted.set(true);
    this.failure.set(null);
    this.confirmation.set('');

    if (this.form.invalid || this.busy()) {
      return;
    }

    const { note } = this.form.getRawValue();
    this.busy.set(true);

    this.api.decide(this.id(), decision, note).subscribe({
      next: (updated) => {
        this.busy.set(false);
        this.submitted.set(false);
        this.approval.set(updated);
        this.form.reset({ note: '' });
        this.confirmation.set(
          decision === 'approve'
            ? `${updated.reference} approved. The branch that raised it has been notified.`
            : `${updated.reference} rejected. The branch that raised it has been notified.`,
        );
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }
}
