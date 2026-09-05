import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { NoticesApi } from '../../core/api/notices.api';
import { ApiError } from '../../core/models/api-error.model';
import { Notice } from '../../core/models/domain.model';
import { SessionService } from '../../core/services/session.service';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  SkeletonComponent,
  StatusPillComponent,
} from '../../shared';
import { NoticeBodyComponent } from './notice-body.component';

/**
 * The notice board.
 *
 * Cards rather than a grid: a notice is a paragraph of prose the duty supervisor wrote,
 * and squeezing it into a table cell made the board unreadable during the Gothenburg
 * customs backlog. The feed comes back whole, so the split into current and finished
 * notices is done here rather than asking the API twice.
 */
@Component({
  selector: 'mrd-notices-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    RouterLink,
    EmptyStateComponent,
    ErrorBannerComponent,
    NoticeBodyComponent,
    PageHeaderComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Service notices"
        subtitle="Operational messages shown across the console"
      >
        @if (canPost()) {
          <a actions class="btn btn--sm btn--primary" routerLink="/notices/new">Post a notice</a>
        }
      </mrd-page-header>

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      @if (loading()) {
        <div class="card board__loading">
          <mrd-skeleton [rows]="6" />
        </div>
      } @else if (notices().length === 0) {
        <div class="card">
          <mrd-empty-state
            title="Nothing posted"
            message="No service notice has been published. The board is used for customs holds, EDI outages and quarter-end cut-off times."
          />
        </div>
      } @else {
        <section class="board">
          <div class="board__head">
            <h2>Showing now</h2>
            <span class="muted small">{{ showingNow().length }} in their publication window</span>
          </div>

          @if (showingNow().length === 0) {
            <div class="card">
              <mrd-empty-state
                title="Nothing showing"
                message="No notice is inside its publication window at the moment."
              />
            </div>
          } @else {
            <ul class="board__list">
              @for (notice of showingNow(); track notice.id) {
                <li class="card notice-card" [class]="'notice-card--' + notice.severity">
                  <div class="notice-card__head">
                    <h3>{{ notice.title }}</h3>
                    <mrd-status-pill [status]="notice.severity" />
                  </div>
                  <div class="notice-card__body">
                    <mrd-notice-body [notice]="notice" />
                  </div>
                  <p class="notice-card__meta muted small">
                    {{ notice.author }} · shown from
                    {{ notice.publishedFrom | date: 'd MMM y, HH:mm' }}
                    @if (notice.publishedTo) {
                      until {{ notice.publishedTo | date: 'd MMM y, HH:mm' }}
                    } @else {
                      until it is withdrawn
                    }
                  </p>
                </li>
              }
            </ul>
          }
        </section>

        @if (expired().length > 0) {
          <section class="board">
            <div class="board__head">
              <h2>Expired</h2>
              <span class="muted small">Outside their publication window — finished or not yet due</span>
            </div>
            <ul class="board__list">
              @for (notice of expired(); track notice.id) {
                <li class="card notice-card notice-card--past">
                  <div class="notice-card__head">
                    <h3>{{ notice.title }}</h3>
                    <mrd-status-pill [status]="notice.severity" />
                  </div>
                  <div class="notice-card__body">
                    <mrd-notice-body [notice]="notice" />
                  </div>
                  <p class="notice-card__meta muted small">
                    {{ notice.author }} ·
                    {{ notice.publishedFrom | date: 'd MMM y, HH:mm' }}
                    @if (notice.publishedTo) {
                      to {{ notice.publishedTo | date: 'd MMM y, HH:mm' }}
                    } @else {
                      · withdrawn
                    }
                  </p>
                </li>
              }
            </ul>
          </section>
        }
      }
    </div>
  `,
  styles: [
    `
      .board {
        margin-bottom: 24px;
      }

      .board__loading {
        padding: 16px;
      }

      .board__head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 10px;
      }

      .board__head h2 {
        margin: 0;
      }

      .board__list {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }

      .notice-card {
        padding: 14px 16px;
        border-left: 4px solid var(--mrd-accent);
      }

      .notice-card--warning {
        border-left-color: var(--mrd-amber);
      }

      .notice-card--critical {
        border-left-color: var(--mrd-danger);
      }

      .notice-card--past {
        border-left-color: var(--mrd-line-strong);
        background: var(--mrd-surface-alt);
      }

      .notice-card__head {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 6px;
      }

      .notice-card__head h3 {
        margin: 0;
      }

      .notice-card__body {
        color: var(--mrd-ink-soft);
      }

      .notice-card__meta {
        margin: 8px 0 0;
      }
    `,
  ],
})
export class NoticesListComponent {
  private readonly api = inject(NoticesApi);
  private readonly session = inject(SessionService);

  readonly notices = signal<Notice[]>([]);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  /** Viewers read the board; posting starts at analyst, as the route gate says. */
  readonly canPost = computed(() => this.session.hasRole('analyst'));

  readonly showingNow = computed(() => this.notices().filter((notice) => isShowing(notice)));
  readonly expired = computed(() => this.notices().filter((notice) => !isShowing(notice)));

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.list().subscribe({
      next: (notices) => {
        // Newest first: the board is read top-down at the start of a shift.
        this.notices.set([...notices].sort(byNewestFirst));
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }
}

/** True while the notice sits inside its publication window and has not been withdrawn. */
function isShowing(notice: Notice): boolean {
  if (notice.active === false) {
    return false;
  }

  const now = Date.now();
  const from = Date.parse(notice.publishedFrom);
  const to = notice.publishedTo === null ? Number.POSITIVE_INFINITY : Date.parse(notice.publishedTo);

  // An unparseable bound is treated as open, so a malformed date never hides a notice.
  return (Number.isNaN(from) || from <= now) && (Number.isNaN(to) || to >= now);
}

function byNewestFirst(left: Notice, right: Notice): number {
  const a = Date.parse(left.publishedFrom);
  const b = Date.parse(right.publishedFrom);
  return (Number.isNaN(b) ? 0 : b) - (Number.isNaN(a) ? 0 : a);
}
