import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

/** Row of filters above a list; the controls themselves are projected in. */
@Component({
  selector: 'mrd-filter-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="filters">
      <div class="filters__fields">
        <ng-content />
      </div>
      <div class="filters__actions">
        <ng-content select="[extraActions]" />
        @if (showReset()) {
          <button type="button" class="btn btn--sm btn--ghost" (click)="reset.emit()">
            Clear filters
          </button>
        }
      </div>
    </div>
  `,
  styles: [
    `
      .filters {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
        padding: 12px 16px;
        background: var(--mrd-surface);
        border: 1px solid var(--mrd-line);
        border-radius: var(--mrd-radius);
        margin-bottom: 16px;
      }

      .filters__fields {
        display: flex;
        align-items: flex-end;
        gap: 12px;
        flex-wrap: wrap;
      }

      .filters__fields .field {
        margin-bottom: 0;
        min-width: 180px;
      }

      .filters__actions {
        display: flex;
        align-items: center;
        gap: 8px;
      }
    `,
  ],
})
export class FilterBarComponent {
  readonly showReset = input(true);
  readonly reset = output<void>();
}
