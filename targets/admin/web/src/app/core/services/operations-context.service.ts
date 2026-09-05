import { Injectable, inject, signal } from '@angular/core';

import { ApprovalsApi } from '../api/approvals.api';
import { IntakeApi } from '../api/intake.api';

/**
 * The handful of live counters the console shows outside of any one screen: the top bar
 * badge and the counters operators embed in an operations notice.
 */
@Injectable({ providedIn: 'root' })
export class OperationsContextService {
  private readonly approvals = inject(ApprovalsApi);
  private readonly intake = inject(IntakeApi);

  private readonly queueDepthSignal = signal(0);
  private readonly openApprovalsSignal = signal(0);
  private readonly refreshedAtSignal = signal<Date | null>(null);

  readonly queueDepth = this.queueDepthSignal.asReadonly();
  readonly openApprovals = this.openApprovalsSignal.asReadonly();
  readonly refreshedAt = this.refreshedAtSignal.asReadonly();

  refresh(): void {
    this.approvals.list({ state: 'pending', page: 1 }).subscribe({
      next: (page) => this.openApprovalsSignal.set(page.total),
      error: () => {
        // The counters are decoration; a failure here must not disturb the screen.
      },
    });

    this.intake.history(1).subscribe({
      next: (page) => {
        this.queueDepthSignal.set(page.items.filter((row) => row.state === 'processing').length);
        this.refreshedAtSignal.set(new Date());
      },
      error: () => {},
    });
  }
}
