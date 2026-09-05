import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { WorkspaceApi } from '../../core/api/workspace.api';
import { ApiError } from '../../core/models/api-error.model';
import { Profile } from '../../core/models/domain.model';
import { SessionService } from '../../core/services/session.service';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';

/** Settings landing: the two things an operator can change about their own console. */
@Component({
  selector: 'mrd-settings-home',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
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
        title="Settings"
        subtitle="How Meridian behaves for you, on this account"
      />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <section class="grid grid--3">
        <a class="card choice" routerLink="/settings/workspace">
          <strong>Console layout</strong>
          <span class="muted small">
            Choose which panels the overview shows and how tightly the tables are packed, then
            keep that arrangement on every workstation you sign in at.
          </span>
        </a>

        <a class="card choice" routerLink="/settings/profile">
          <strong>Your profile</strong>
          <span class="muted small">
            Name, contact details, office, language and time zone — what colleagues see next to
            your entries in the audit trail.
          </span>
        </a>

        <a class="card choice" routerLink="/settings/profile">
          <strong>Daily digest</strong>
          <span class="muted small">
            Decide whether the morning summary of customs holds and invoices due lands in your
            inbox. It lives on the profile screen.
          </span>
        </a>
      </section>

      <section class="card signed-in">
        <div class="card__header">
          <h2>Signed in as</h2>
          <span class="muted small">Read-only — the service desk changes roles</span>
        </div>
        @if (loading()) {
          <div class="card__body"><mrd-skeleton [rows]="3" /></div>
        } @else if (!session.authenticated()) {
          <mrd-empty-state
            title="No session"
            message="Sign in again to see your console settings."
          />
        } @else {
          <div class="card__body">
            <dl class="detail">
              <dt>Name</dt>
              <dd>{{ session.displayName() }}</dd>
              <dt>Role</dt>
              <dd><mrd-status-pill [status]="session.role()" /></dd>
              <dt>Account</dt>
              <dd>{{ session.accountName() || '—' }}</dd>
              <dt>Office</dt>
              <dd>{{ profile()?.office || '—' }}</dd>
              <dt>Time zone</dt>
              <dd>{{ profile()?.timeZone || '—' }}</dd>
            </dl>
          </div>
        }
      </section>
    </div>
  `,
  styles: [
    `
      .choice {
        display: flex;
        flex-direction: column;
        gap: 4px;
        padding: 14px 16px;
        color: inherit;
      }

      .choice:hover {
        text-decoration: none;
        border-color: var(--mrd-line-strong);
      }

      .signed-in {
        margin-top: 16px;
      }
    `,
  ],
})
export class SettingsHomeComponent {
  private readonly api = inject(WorkspaceApi);

  readonly session = inject(SessionService);
  readonly profile = signal<Profile | null>(null);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  constructor() {
    this.load();
  }

  /** Name, role and account come from the session; office and time zone from the record. */
  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.profile().subscribe({
      next: (profile) => {
        this.profile.set(profile);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }
}
