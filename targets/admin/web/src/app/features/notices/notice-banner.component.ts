import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { NoticesApi } from '../../core/api/notices.api';
import { Notice } from '../../core/models/domain.model';
import { NoticeBodyComponent } from './notice-body.component';

const DISMISSED_KEY = 'mrd.notices.dismissed';

/**
 * Strip of operations notices under the top bar.
 *
 * A dismissed notice stays dismissed for that browser only; supervisors expect the
 * banner to come back for everyone else until the notice expires.
 */
@Component({
  selector: 'mrd-notice-banner',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, NoticeBodyComponent],
  template: `
    @if (visible().length > 0) {
      <div class="notices">
        @for (notice of visible(); track notice.id) {
          <article class="notice" [class]="'notice--' + notice.severity">
            <div class="notice__main">
              <h2 class="notice__title">{{ notice.title }}</h2>
              <mrd-notice-body [notice]="notice" />
              <p class="notice__meta">
                Posted by {{ notice.author }} · from
                {{ notice.publishedFrom | date: 'd MMM y, HH:mm' }}
                @if (notice.publishedTo) {
                  until {{ notice.publishedTo | date: 'd MMM y, HH:mm' }}
                }
              </p>
            </div>
            <button
              type="button"
              class="notice__close"
              [attr.aria-label]="'Dismiss notice ' + notice.title"
              (click)="dismiss(notice)"
            >
              ×
            </button>
          </article>
        }
      </div>
    }
  `,
  styleUrl: './notice-banner.component.scss',
})
export class NoticeBannerComponent {
  private readonly api = inject(NoticesApi);

  private readonly notices = signal<Notice[]>([]);
  private readonly dismissed = signal<string[]>(readDismissed());

  readonly visible = computed(() =>
    this.notices().filter((notice) => !this.dismissed().includes(notice.id)),
  );

  constructor() {
    this.api.list().subscribe({
      next: (notices) => this.notices.set(notices.filter(isCurrent)),
      error: () => {
        // A missing notice feed is not worth an error banner across the console.
        this.notices.set([]);
      },
    });
  }

  dismiss(notice: Notice): void {
    const next = [...this.dismissed(), notice.id];
    this.dismissed.set(next);
    try {
      window.localStorage.setItem(DISMISSED_KEY, JSON.stringify(next.slice(-50)));
    } catch {
      // Storage is optional; the notice simply reappears on the next load.
    }
  }
}

function isCurrent(notice: Notice): boolean {
  if (notice.active === false) {
    return false;
  }
  const now = Date.now();
  const from = Date.parse(notice.publishedFrom);
  const to = notice.publishedTo === null ? Number.POSITIVE_INFINITY : Date.parse(notice.publishedTo);
  return (Number.isNaN(from) || from <= now) && (Number.isNaN(to) || to >= now);
}

function readDismissed(): string[] {
  try {
    const raw = window.localStorage.getItem(DISMISSED_KEY);
    const parsed: unknown = raw === null ? [] : JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string') : [];
  } catch {
    return [];
  }
}
