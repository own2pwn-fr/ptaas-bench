import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { pageCount } from '../core/models/page.model';

/** Pager shown under every list. Pages are one-based, as the API numbers them. */
@Component({
  selector: 'mrd-pagination',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <nav class="pager" aria-label="Pagination">
      <span class="muted small">{{ rangeLabel() }}</span>
      <span class="spacer"></span>
      <button
        type="button"
        class="btn btn--sm"
        [disabled]="page() <= 1"
        (click)="pageChange.emit(page() - 1)"
      >
        Previous
      </button>
      <span class="small">Page {{ page() }} of {{ pages() }}</span>
      <button
        type="button"
        class="btn btn--sm"
        [disabled]="page() >= pages()"
        (click)="pageChange.emit(page() + 1)"
      >
        Next
      </button>
    </nav>
  `,
  styles: [
    `
      .pager {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 16px;
        border-top: 1px solid var(--mrd-line);
        background: var(--mrd-surface-alt);
      }
    `,
  ],
})
export class PaginationComponent {
  readonly page = input(1);
  readonly size = input(25);
  readonly total = input(0);

  readonly pageChange = output<number>();

  readonly pages = computed(() => pageCount({ size: this.size(), total: this.total() }));

  readonly rangeLabel = computed(() => {
    const total = this.total();
    if (total === 0) {
      return 'No records';
    }
    const first = (this.page() - 1) * this.size() + 1;
    const last = Math.min(total, this.page() * this.size());
    return `${first}–${last} of ${total}`;
  });
}
