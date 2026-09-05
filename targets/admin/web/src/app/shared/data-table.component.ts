import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { EmptyStateComponent } from './empty-state.component';
import { SkeletonComponent } from './skeleton.component';
import { StatusPillComponent } from './status-pill.component';

export interface TableColumn<T = Record<string, unknown>> {
  /** Property read from the row when no `value` function is given. */
  key: string;
  label: string;
  /** Cell text; use it for formatting, joining or fallbacks. */
  value?: (row: T) => string | number | null | undefined;
  align?: 'start' | 'end';
  width?: string;
  /** Render the cell as a status pill instead of plain text. */
  pill?: boolean;
  /** Render the cell in the monospace face — references, ids, hashes. */
  mono?: boolean;
}

/**
 * The list table used by every area.
 *
 * It stays dumb on purpose: columns describe how to read a cell out of a row, the
 * feature keeps the data. Anything richer than a pill or a link belongs in a screen of
 * its own rather than in another table option.
 */
@Component({
  selector: 'mrd-data-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, EmptyStateComponent, SkeletonComponent, StatusPillComponent],
  template: `
    @if (loading()) {
      <div class="table-loading">
        <mrd-skeleton [rows]="skeletonRows()" />
      </div>
    } @else if (rows().length === 0) {
      <mrd-empty-state [title]="emptyTitle()" [message]="emptyMessage()">
        <ng-content select="[emptyAction]" />
      </mrd-empty-state>
    } @else {
      <div class="table-wrap">
        <table class="data">
          <thead>
            <tr>
              @for (column of columns(); track column.key) {
                <th
                  [style.width]="column.width"
                  [class.numeric]="column.align === 'end'"
                  scope="col"
                >
                  {{ column.label }}
                </th>
              }
            </tr>
          </thead>
          <tbody>
            @for (row of rows(); track rowKey(row, $index)) {
              <tr (click)="rowSelect.emit(row)">
                @for (column of columns(); track column.key) {
                  <td [class.numeric]="column.align === 'end'" [class.mono]="column.mono">
                    @if (column.pill) {
                      <mrd-status-pill [status]="cellText(row, column)" />
                    } @else if ($first && link() !== null) {
                      <a [routerLink]="linkFor(row)">{{ cellText(row, column) }}</a>
                    } @else {
                      {{ cellText(row, column) }}
                    }
                  </td>
                }
              </tr>
            }
          </tbody>
        </table>
      </div>
    }
  `,
  styles: [
    `
      .table-wrap {
        overflow-x: auto;
      }

      .table-loading {
        padding: 12px 16px;
      }

      td.mono {
        font-family: var(--mrd-mono);
        font-size: 12.5px;
      }
    `,
  ],
})
export class DataTableComponent {
  readonly columns = input.required<TableColumn<never>[]>();
  readonly rows = input<readonly unknown[]>([]);
  readonly loading = input(false);
  readonly skeletonRows = input(6);
  readonly emptyTitle = input('Nothing to show');
  readonly emptyMessage = input('');
  /** Property used to track rows; falls back to the row index. */
  readonly trackBy = input('id');
  /** When set, the first column of each row becomes a link to this route. */
  readonly link = input<((row: never) => unknown[]) | null>(null);

  readonly rowSelect = output<unknown>();

  cellText(row: unknown, column: TableColumn<never>): string {
    const record = row as Record<string, unknown>;
    const raw = column.value ? column.value(row as never) : record[column.key];
    if (raw === null || raw === undefined || raw === '') {
      return '—';
    }
    return String(raw);
  }

  linkFor(row: unknown): unknown[] {
    const factory = this.link();
    return factory === null ? [] : factory(row as never);
  }

  rowKey(row: unknown, index: number): unknown {
    const record = row as Record<string, unknown>;
    return record[this.trackBy()] ?? index;
  }
}
