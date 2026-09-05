import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';

export interface DiagnosticsReport {
  component: string;
  [detail: string]: unknown;
}

/**
 * Client-side diagnostics.
 *
 * Failures in the browser never reach the API logs on their own, so the global error
 * handler and the few components that render something they did not author post a
 * short report here. Delivery is best effort — a failed report must never surface to
 * the operator.
 */
@Injectable({ providedIn: 'root' })
export class DiagnosticsService {
  private readonly http = inject(HttpClient);

  report(payload: DiagnosticsReport): void {
    this.http.post('/api/client/diagnostics', payload).subscribe({ error: () => {} });
  }
}
