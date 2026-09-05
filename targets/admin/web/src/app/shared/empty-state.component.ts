import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** Shown instead of an empty table, with room for a call to action. */
@Component({
  selector: 'mrd-empty-state',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="empty">
      <div class="empty__mark" aria-hidden="true">◍</div>
      <h3>{{ title() }}</h3>
      @if (message()) {
        <p class="muted small">{{ message() }}</p>
      }
      <ng-content />
    </div>
  `,
  styles: [
    `
      .empty {
        padding: 40px 24px;
        text-align: center;
        color: var(--mrd-ink-soft);
      }

      .empty__mark {
        font-size: 26px;
        color: var(--mrd-line-strong);
        margin-bottom: 8px;
      }

      .empty h3 {
        margin-bottom: 4px;
      }
    `,
  ],
})
export class EmptyStateComponent {
  readonly title = input('Nothing to show');
  readonly message = input('');
}
