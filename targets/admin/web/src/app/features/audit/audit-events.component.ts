import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { debounceTime } from 'rxjs';

import { toApiError } from '../../core/api/api-error.util';
import { AuditApi } from '../../core/api/audit.api';
import { ApiError } from '../../core/models/api-error.model';
import { AuditEvent } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  FilterBarComponent,
  PageHeaderComponent,
  PaginationComponent,
  SkeletonComponent,
  StatusPillComponent,
  TableColumn,
} from '../../shared';

/**
 * Actions the console records often enough to be worth a filter of their own. The API
 * accepts any action name; this list is what the desk actually asks for when someone
 * chases a change on an account or a rate.
 */
const ACTIONS: readonly string[] = [
  'account.updated',
  'invoice.issued',
  'consignment.status-changed',
  'rule.published',
  'approval.decided',
  'member.invited',
  'layout.restored',
  'credential.stored',
];

const MOMENT = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

/** Timestamps in the grid are absolute: an audit answer has to survive being printed. */
function formatMoment(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : MOMENT.format(parsed);
}

interface AttributeRow {
  key: string;
  value: string;
}

/**
 * The audit trail.
 *
 * The grid is kept lean on purpose: the list call asks only for the event rows, which
 * carry the actor's display name and nothing else. The staff record behind an actor is
 * expanded only when an operator opens the drawer on a row, because joining every page
 * of the trail to the directory is the expensive half of the query and almost nobody
 * reads more than one or two events per visit.
 */
@Component({
  selector: 'mrd-audit-events',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    RouterLink,
    DataTableComponent,
    ErrorBannerComponent,
    FilterBarComponent,
    PageHeaderComponent,
    PaginationComponent,
    SkeletonComponent,
    StatusPillComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header
        title="Audit trail"
        subtitle="Every change recorded in Meridian, kept for the retention window"
      />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <mrd-filter-bar (reset)="clear()">
        <div class="field">
          <label for="audit-actor">Actor</label>
          <input
            id="audit-actor"
            type="search"
            [formControl]="actor"
            placeholder="Name or sign-in, e.g. H. Lindqvist"
          />
        </div>
        <div class="field">
          <label for="audit-action">Action</label>
          <select id="audit-action" [formControl]="action">
            <option value="">All actions</option>
            @for (choice of actions; track choice) {
              <option [value]="choice">{{ choice }}</option>
            }
          </select>
        </div>
      </mrd-filter-bar>

      <div class="trail" [class.trail--split]="selected() !== null">
        <div class="card">
          <mrd-data-table
            [columns]="columns"
            [rows]="page().items"
            [loading]="loading()"
            [link]="eventLink"
            (rowSelect)="openDrawer($event)"
            emptyTitle="Nothing recorded"
            emptyMessage="No event matches these filters. Widen the actor or pick another action."
          />
          <mrd-pagination
            [page]="page().page"
            [size]="page().size"
            [total]="page().total"
            (pageChange)="goToPage($event)"
          />
        </div>

        @if (selected(); as event) {
          <aside class="card drawer" aria-label="Event detail">
            <div class="card__header">
              <h2>Event detail</h2>
              <button type="button" class="btn btn--sm btn--ghost" (click)="closeDrawer()">
                Close
              </button>
            </div>

            <div class="card__body">
              <mrd-error-banner
                [error]="drawerFailure()"
                heading="The actor record could not be loaded"
                (retry)="expandActor(event)"
                (dismiss)="drawerFailure.set(null)"
              />

              <dl class="detail">
                <dt>Recorded</dt>
                <dd>{{ moment(event.at) }}</dd>
                <dt>Actor</dt>
                <dd>{{ event.actor }}</dd>
                <dt>Action</dt>
                <dd class="mono">{{ event.action }}</dd>
                <dt>Target</dt>
                <dd>{{ event.target }}</dd>
                <dt>Outcome</dt>
                <dd><mrd-status-pill [status]="event.outcome" /></dd>
                <dt>Source address</dt>
                <dd class="mono">{{ event.sourceAddress }}</dd>
              </dl>

              <h3 class="drawer__title">Who this was</h3>
              @if (drawerLoading()) {
                <mrd-skeleton [rows]="4" />
              } @else {
                @if (event.actorDetail; as person) {
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
                } @else {
                  <p class="muted small">
                    The staff directory holds no current record for this actor — the account
                    may have been closed since the event was written.
                  </p>
                }
              }

              <h3 class="drawer__title">Attributes</h3>
              @if (attributes().length === 0) {
                <p class="muted small">This action was recorded without extra attributes.</p>
              } @else {
                <dl class="detail">
                  @for (entry of attributes(); track entry.key) {
                    <dt>{{ entry.key }}</dt>
                    <dd class="mono">{{ entry.value }}</dd>
                  }
                </dl>
              }
            </div>

            <div class="card__footer">
              <a class="btn btn--sm" [routerLink]="['/audit', event.id]">Open full record</a>
            </div>
          </aside>
        }
      </div>
    </div>
  `,
  styles: [
    `
      .trail {
        display: grid;
        gap: 16px;
      }

      .trail--split {
        grid-template-columns: minmax(0, 1fr) 380px;
        align-items: start;
      }

      @media (max-width: 1100px) {
        .trail--split {
          grid-template-columns: minmax(0, 1fr);
        }
      }

      .drawer {
        position: sticky;
        top: calc(var(--mrd-topbar-height) + 16px);
      }

      .drawer .detail {
        grid-template-columns: 120px 1fr;
      }

      .drawer__title {
        margin: 18px 0 8px;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--mrd-ink-faint);
      }
    `,
  ],
})
export class AuditEventsComponent {
  private readonly api = inject(AuditApi);

  readonly actions = ACTIONS;

  readonly actor = new FormControl('', { nonNullable: true });
  readonly action = new FormControl('', { nonNullable: true });

  readonly page = signal<Page<AuditEvent>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  /** Row currently shown in the side panel, or null when the panel is closed. */
  readonly selected = signal<AuditEvent | null>(null);
  readonly drawerLoading = signal(false);
  readonly drawerFailure = signal<ApiError | null>(null);

  readonly attributes = computed<AttributeRow[]>(() => {
    const source = this.selected()?.attributes;
    if (!source) {
      return [];
    }
    return Object.entries(source).map(([key, value]) => ({
      key,
      value: typeof value === 'string' ? value : JSON.stringify(value),
    }));
  });

  readonly columns: TableColumn<AuditEvent>[] = [
    { key: 'at', label: 'Timestamp', width: '190px', value: (row) => formatMoment(row.at) },
    { key: 'actor', label: 'Actor', width: '170px' },
    { key: 'action', label: 'Action', mono: true, width: '210px' },
    { key: 'target', label: 'Target' },
    { key: 'outcome', label: 'Outcome', pill: true, width: '110px' },
    { key: 'sourceAddress', label: 'Source address', mono: true, width: '170px' },
  ];

  constructor() {
    this.load();

    // The actor box is typed into a word at a time; 300 ms is the shortest pause that
    // stops the desk sending a query per keystroke on a long trail.
    this.actor.valueChanges
      .pipe(debounceTime(300), takeUntilDestroyed())
      .subscribe(() => this.applyFilters());

    this.action.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => this.applyFilters());
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api
      .events({
        actor: this.actor.value.trim(),
        action: this.action.value,
        page: this.page().page,
      })
      .subscribe({
        next: (result) => {
          this.page.set(result);
          this.loading.set(false);
        },
        error: (error: unknown) => {
          this.loading.set(false);
          this.failure.set(toApiError(error));
        },
      });
  }

  goToPage(page: number): void {
    this.closeDrawer();
    this.page.update((current) => ({ ...current, page }));
    this.load();
  }

  clear(): void {
    this.actor.setValue('', { emitEvent: false });
    this.action.setValue('', { emitEvent: false });
    this.applyFilters();
  }

  /** Opening a row shows what the grid already has, then fills in the staff record. */
  openDrawer(row: unknown): void {
    const event = row as AuditEvent;
    this.selected.set(event);
    this.expandActor(event);
  }

  closeDrawer(): void {
    this.selected.set(null);
    this.drawerLoading.set(false);
    this.drawerFailure.set(null);
  }

  /**
   * Re-reads the page the operator is looking at, this time asking the API to expand
   * the actor, and keeps the row that was opened. The grid itself never asks for the
   * expansion, so the common case stays a single flat query.
   */
  expandActor(event: AuditEvent): void {
    this.drawerLoading.set(true);
    this.drawerFailure.set(null);
    this.api.events({ expand: 'actor', page: this.page().page }).subscribe({
      next: (result) => {
        const match = result.items.find((item) => item.id === event.id);
        if (match && this.selected()?.id === event.id) {
          this.selected.set(match);
        }
        this.drawerLoading.set(false);
      },
      error: (error: unknown) => {
        this.drawerLoading.set(false);
        this.drawerFailure.set(toApiError(error));
      },
    });
  }

  moment(value: string): string {
    return formatMoment(value);
  }

  private applyFilters(): void {
    this.closeDrawer();
    this.page.update((current) => ({ ...current, page: 1 }));
    this.load();
  }

  eventLink = (row: AuditEvent): unknown[] => ['/audit', row.id];
}
