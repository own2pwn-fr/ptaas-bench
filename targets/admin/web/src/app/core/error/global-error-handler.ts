import { ErrorHandler, Injectable, inject } from '@angular/core';

import { DiagnosticsService } from '../services/diagnostics.service';

/**
 * Last-resort handler.
 *
 * Anything that escapes a feature's own error handling ends up here: it is logged for
 * the operator's browser console and reported so the platform team sees front-end
 * failures without waiting for a support call.
 */
@Injectable()
export class GlobalErrorHandler implements ErrorHandler {
  private readonly diagnostics = inject(DiagnosticsService);

  handleError(error: unknown): void {
    console.error(error);

    const detail = error instanceof Error ? error : new Error(String(error));
    this.diagnostics.report({
      component: 'global-error-handler',
      name: detail.name,
      message: detail.message,
      stack: detail.stack ?? null,
      path: window.location.pathname + window.location.search,
      at: new Date().toISOString(),
    });
  }
}
