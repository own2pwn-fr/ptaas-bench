import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { ApiError } from '../../core/models/api-error.model';
import { SessionService } from '../../core/services/session.service';
import { ErrorBannerComponent, FieldErrorComponent } from '../../shared';

/**
 * Account recovery.
 *
 * The reference is either the operator's work email or their staff number. The response
 * is intentionally identical whether or not the reference matched, so the screen shows
 * the same confirmation in both cases.
 */
@Component({
  selector: 'mrd-account-recovery',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule, RouterLink, ErrorBannerComponent, FieldErrorComponent],
  templateUrl: './account-recovery.component.html',
  styleUrl: './auth.scss',
})
export class AccountRecoveryComponent {
  private readonly fb = inject(FormBuilder);
  private readonly session = inject(SessionService);

  readonly form = this.fb.nonNullable.group({
    reference: ['', [Validators.required, Validators.minLength(3)]],
  });

  readonly busy = signal(false);
  readonly submitted = signal(false);
  readonly sent = signal(false);
  readonly failure = signal<ApiError | null>(null);

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);

    if (this.form.invalid || this.busy()) {
      return;
    }

    this.busy.set(true);
    this.session.recover(this.form.getRawValue().reference).subscribe({
      next: () => {
        this.busy.set(false);
        this.sent.set(true);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.failure.set(toApiError(error));
      },
    });
  }
}
