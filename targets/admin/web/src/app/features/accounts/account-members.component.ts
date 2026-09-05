import { ChangeDetectionStrategy, Component, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AccountsApi } from '../../core/api/accounts.api';
import { toApiError } from '../../core/api/api-error.util';
import { ApiError } from '../../core/models/api-error.model';
import { AccountMember } from '../../core/models/domain.model';
import { EMPTY_PAGE, Page } from '../../core/models/page.model';
import {
  DataTableComponent,
  ErrorBannerComponent,
  PageHeaderComponent,
  PaginationComponent,
  TableColumn,
} from '../../shared';
import { AccountTabsComponent } from './account-tabs.component';

interface MemberColumnChoice {
  field: string;
  label: string;
  align?: 'start' | 'end';
  pill?: boolean;
  /** Kept in the grid at all times so a row can still be identified. */
  fixed?: boolean;
}

/** Columns the picker offers, in the order the grid shows them. */
const COLUMN_CHOICES: MemberColumnChoice[] = [
  { field: 'id', label: 'Id', fixed: true },
  { field: 'displayName', label: 'Name', fixed: true },
  { field: 'role', label: 'Role', pill: true },
  { field: 'jobTitle', label: 'Job title' },
  { field: 'email', label: 'Email' },
  { field: 'phone', label: 'Phone' },
  { field: 'status', label: 'Status', pill: true },
  { field: 'lastSeenAt', label: 'Last seen' },
];

/**
 * The people who can act on a customer account.
 *
 * The grid has a column picker and asks the API only for the columns the operator kept:
 * pulling every attribute of every member for a grid showing three of them was the
 * slowest call in the console.
 */
@Component({
  selector: 'mrd-account-members',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink,
    AccountTabsComponent,
    DataTableComponent,
    ErrorBannerComponent,
    PageHeaderComponent,
    PaginationComponent,
  ],
  template: `
    <div class="page">
      <mrd-page-header title="Account members" subtitle="Who may act on this account">
        <a actions class="btn btn--sm" [routerLink]="['/orgs', orgId()]">Back to account</a>
      </mrd-page-header>

      <mrd-account-tabs [orgId]="orgId()" />

      <mrd-error-banner [error]="failure()" (retry)="load()" (dismiss)="failure.set(null)" />

      <div class="card">
        <div class="card__header">
          <h2>Members</h2>
          <details class="picker">
            <summary class="btn btn--sm">Columns</summary>
            <div class="picker__menu">
              @for (choice of choices; track choice.field) {
                <label class="picker__row">
                  <input
                    type="checkbox"
                    [checked]="selected().includes(choice.field)"
                    [disabled]="choice.fixed === true"
                    (change)="toggle(choice.field)"
                  />
                  <span>{{ choice.label }}</span>
                </label>
              }
              <p class="picker__hint small muted">Requested fields: {{ fields() }}</p>
            </div>
          </details>
        </div>

        <mrd-data-table
          [columns]="columns()"
          [rows]="page().items"
          [loading]="loading()"
          emptyTitle="No members"
          emptyMessage="Nobody has been given access to this account yet."
        />
        <mrd-pagination
          [page]="page().page"
          [size]="page().size"
          [total]="page().total"
          (pageChange)="goToPage($event)"
        />
      </div>
    </div>
  `,
  styles: [
    `
      .picker {
        position: relative;
      }

      .picker summary {
        list-style: none;
        cursor: pointer;
      }

      .picker summary::-webkit-details-marker {
        display: none;
      }

      .picker__menu {
        position: absolute;
        right: 0;
        top: calc(100% + 6px);
        z-index: 10;
        min-width: 220px;
        padding: 10px;
        background: var(--mrd-surface);
        border: 1px solid var(--mrd-line);
        border-radius: var(--mrd-radius);
        box-shadow: var(--mrd-shadow);
      }

      .picker__row {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 3px 2px;
        font-size: 13px;
      }

      .picker__row input {
        width: auto;
      }

      .picker__hint {
        margin: 8px 0 0;
        padding-top: 8px;
        border-top: 1px solid var(--mrd-line);
        word-break: break-all;
      }
    `,
  ],
})
export class AccountMembersComponent {
  readonly orgId = input.required<string>();

  private readonly api = inject(AccountsApi);

  readonly choices = COLUMN_CHOICES;
  readonly selected = signal<string[]>(['id', 'displayName', 'role']);
  readonly fields = signal('id,displayName,role');
  readonly page = signal<Page<AccountMember>>(EMPTY_PAGE);
  readonly loading = signal(true);
  readonly failure = signal<ApiError | null>(null);

  readonly columns = signal<TableColumn<AccountMember>[]>(buildColumns(['id', 'displayName', 'role']));

  constructor() {
    effect(() => {
      if (this.orgId()) {
        this.load();
      }
    });
  }

  toggle(field: string): void {
    const choice = COLUMN_CHOICES.find((entry) => entry.field === field);
    if (choice?.fixed) {
      return;
    }

    const next = this.selected().includes(field)
      ? this.selected().filter((entry) => entry !== field)
      : [...this.selected(), field];

    // Keep the picker's order rather than the click order, so the grid stays stable.
    const ordered = COLUMN_CHOICES.filter((entry) => next.includes(entry.field)).map(
      (entry) => entry.field,
    );

    this.selected.set(ordered);
    this.fields.set(ordered.join(','));
    this.columns.set(buildColumns(ordered));
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.failure.set(null);
    this.api.members(this.orgId(), this.fields()).subscribe({
      next: (page) => {
        this.page.set(page);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }

  goToPage(page: number): void {
    // The members list comes back in a single page today; the pager is here so the grid
    // behaves like every other list once an account grows past one screenful.
    this.page.update((current) => ({ ...current, page }));
    this.load();
  }
}

function buildColumns(fields: string[]): TableColumn<AccountMember>[] {
  return fields.map((field) => {
    const choice = COLUMN_CHOICES.find((entry) => entry.field === field);
    return {
      key: field,
      label: choice?.label ?? field,
      align: choice?.align,
      pill: choice?.pill,
      mono: field === 'id',
    };
  });
}
