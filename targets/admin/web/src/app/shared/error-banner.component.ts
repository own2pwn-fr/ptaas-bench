import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { ApiError } from '../core/models/api-error.model';

/** The one way the console shows a failed API call. */
@Component({
  selector: 'mrd-error-banner',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (error(); as failure) {
      <div class="banner" role="alert">
        <div class="banner__body">
          <strong>{{ heading() }}</strong>
          <span>{{ failure.message }}</span>
          @if (failure.reference) {
            <span class="muted small">Reference {{ failure.reference }}</span>
          }
        </div>
        <div class="banner__actions">
          @if (retryable()) {
            <button type="button" class="btn btn--sm" (click)="retry.emit()">Try again</button>
          }
          <button type="button" class="btn btn--sm btn--ghost" (click)="dismiss.emit()">
            Dismiss
          </button>
        </div>
      </div>
    }
  `,
  styles: [
    `
      .banner {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        padding: 10px 14px;
        margin-bottom: 16px;
        background: var(--mrd-danger-soft);
        border: 1px solid #eabfb9;
        border-radius: var(--mrd-radius-sm);
        color: #6f1d15;
      }

      .banner__body {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }

      .banner__actions {
        display: flex;
        gap: 6px;
      }
    `,
  ],
})
export class ErrorBannerComponent {
  readonly error = input<ApiError | null>(null);
  readonly heading = input('That request did not go through');
  readonly retryable = input(true);

  readonly retry = output<void>();
  readonly dismiss = output<void>();
}
