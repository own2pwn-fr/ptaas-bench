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
import { DirectoryApi } from '../../core/api/directory.api';
import { ApiError } from '../../core/models/api-error.model';
import { Person } from '../../core/models/domain.model';
import {
  EmptyStateComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  SkeletonComponent,
} from '../../shared';

/** One directory entry: how to reach a colleague and who they report to. */
@Component({
  selector: 'mrd-person-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    RouterLink,
    EmptyStateComponent,
    ErrorBannerComponent,
    PageHeaderComponent,
    SkeletonComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        [title]="person()?.displayName ?? 'Directory entry'"
        [subtitle]="person() ? person()!.jobTitle + ' · ' + person()!.department : 'Loading…'"
      >
        <a actions class="btn btn--sm" routerLink="/directory">Back to directory</a>
      </mrd-page-header>

      <mrd-error-banner
        [error]="failure()"
        heading="The directory entry could not be read"
        (retry)="load()"
        (dismiss)="failure.set(null)"
      />

      @if (loading()) {
        <div class="card card--pad">
          <mrd-skeleton [rows]="6" />
        </div>
      } @else if (missing()) {
        <div class="card">
          <mrd-empty-state
            title="No such entry"
            message="This colleague is no longer in the directory. People are removed on their last working day; ask HR if you need historic contact details."
          >
            <a class="btn btn--sm btn--primary" routerLink="/directory">Search the directory</a>
          </mrd-empty-state>
        </div>
      } @else if (person()) {
        @let record = person()!;
        <div class="grid grid--2">
          <section class="card">
            <div class="card__body person">
              <span class="person__avatar" aria-hidden="true">{{ initials() }}</span>
              <div class="person__text">
                <h2>{{ record.displayName }}</h2>
                <p class="muted small">{{ record.jobTitle }}</p>
                <p class="small">{{ record.department }} · {{ record.office }}</p>
                <p class="small">
                  <a [href]="'mailto:' + record.email">{{ record.email }}</a>
                  · extension <span class="mono">{{ record.extension }}</span>
                </p>
              </div>
            </div>
            <div class="card__footer small muted">
              @if (record.startedOn) {
                Started on {{ record.startedOn | date: 'd MMM y' }}
              } @else {
                Start date not recorded
              }
            </div>
          </section>

          <section class="card">
            <div class="card__header"><h2>Details</h2></div>
            <div class="card__body">
              <dl class="detail">
                <dt>Given name</dt>
                <dd>{{ record.givenName }}</dd>
                <dt>Surname</dt>
                <dd>{{ record.surname }}</dd>
                <dt>Department</dt>
                <dd>{{ record.department }}</dd>
                <dt>Office</dt>
                <dd>{{ record.office }}</dd>
                <dt>Extension</dt>
                <dd class="mono">{{ record.extension }}</dd>
                <dt>Directory id</dt>
                <dd class="mono">{{ record.uid }}</dd>
                <dt>Reports to</dt>
                <dd>
                  @if (record.managerUid) {
                    <a [routerLink]="['/directory', record.managerUid]">
                      {{ record.managerName || record.managerUid }}
                    </a>
                  } @else {
                    <span class="muted">Not recorded</span>
                  }
                </dd>
              </dl>
            </div>
          </section>
        </div>
      }
    </div>
  `,
  styles: [
    `
      .card--pad {
        padding: 16px;
      }

      .person {
        display: flex;
        align-items: flex-start;
        gap: 16px;
      }

      .person__avatar {
        display: flex;
        align-items: center;
        justify-content: center;
        flex: 0 0 auto;
        width: 56px;
        height: 56px;
        border-radius: 50%;
        background: var(--mrd-accent-soft);
        color: var(--mrd-accent-ink);
        font-size: 19px;
        font-weight: 600;
        letter-spacing: 0.03em;
      }

      .person__text h2 {
        margin-bottom: 2px;
      }

      .person__text p {
        margin: 0 0 2px;
      }
    `,
  ],
})
export class PersonDetailComponent {
  /** Bound from the route path by the router's component input binding. */
  readonly uid = input.required<string>();

  private readonly api = inject(DirectoryApi);

  readonly person = signal<Person | null>(null);
  readonly loading = signal(true);
  readonly missing = signal(false);
  readonly failure = signal<ApiError | null>(null);

  /** Initials for the avatar; the directory has no photographs. */
  readonly initials = computed(() => {
    const record = this.person();
    if (record === null) {
      return '—';
    }
    const letters = `${record.givenName.charAt(0)}${record.surname.charAt(0)}`.trim();
    return letters.toUpperCase() || record.displayName.charAt(0).toUpperCase() || '—';
  });

  constructor() {
    effect(() => {
      if (this.uid()) {
        this.load();
      }
    });
  }

  load(): void {
    this.loading.set(true);
    this.missing.set(false);
    this.failure.set(null);
    this.api.person(this.uid()).subscribe({
      next: (record) => {
        this.person.set(record);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        const failure = toApiError(error);
        this.loading.set(false);
        this.person.set(null);
        // A removed colleague is an ordinary outcome here, not a failure worth a banner.
        if (failure.status === 404) {
          this.missing.set(true);
          return;
        }
        this.failure.set(failure);
      },
    });
  }
}
