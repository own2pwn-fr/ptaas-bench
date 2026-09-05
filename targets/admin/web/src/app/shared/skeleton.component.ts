import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Placeholder bars used while a list or a panel is loading. */
@Component({
  selector: 'mrd-skeleton',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="skeleton" role="status" aria-live="polite" aria-label="Loading">
      @for (row of placeholders(); track $index) {
        <div class="skeleton__row" [style.width.%]="row"></div>
      }
    </div>
  `,
  styles: [
    `
      .skeleton {
        padding: 12px 0;
        display: flex;
        flex-direction: column;
        gap: 10px;
      }

      .skeleton__row {
        height: 12px;
        border-radius: 3px;
        background: linear-gradient(
          90deg,
          var(--mrd-surface-sunken) 25%,
          var(--mrd-surface-alt) 37%,
          var(--mrd-surface-sunken) 63%
        );
        background-size: 400% 100%;
        animation: shimmer 1.4s ease-in-out infinite;
      }

      @keyframes shimmer {
        0% {
          background-position: 100% 0;
        }
        100% {
          background-position: 0 0;
        }
      }

      @media (prefers-reduced-motion: reduce) {
        .skeleton__row {
          animation: none;
        }
      }
    `,
  ],
})
export class SkeletonComponent {
  readonly rows = input(5);

  /** Varying widths read as text rather than as a block. */
  readonly placeholders = computed(() => {
    const widths = [96, 74, 88, 62, 91, 70, 84, 58];
    return Array.from({ length: Math.max(1, this.rows()) }, (_, index) => widths[index % widths.length]);
  });
}
