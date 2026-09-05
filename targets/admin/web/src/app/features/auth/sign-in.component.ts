import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { toApiError } from '../../core/api/api-error.util';
import { ApiError } from '../../core/models/api-error.model';
import { SessionService } from '../../core/services/session.service';
import { ErrorBannerComponent, FieldErrorComponent } from '../../shared';

/** Sign-in screen. The API sets the session cookie; the console only reads context. */
@Component({
  selector: 'mrd-sign-in',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule, RouterLink, ErrorBannerComponent, FieldErrorComponent],
  templateUrl: './sign-in.component.html',
  styleUrl: './auth.scss',
})
export class SignInComponent {
  private readonly fb = inject(FormBuilder);
  private readonly session = inject(SessionService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  readonly form = this.fb.nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', [Validators.required, Validators.minLength(8)]],
  });

  readonly busy = signal(false);
  readonly submitted = signal(false);
  readonly failure = signal<ApiError | null>(null);

  submit(): void {
    this.submitted.set(true);
    this.failure.set(null);

    if (this.form.invalid || this.busy()) {
      return;
    }

    const { email, password } = this.form.getRawValue();
    this.busy.set(true);

    this.session.login(email, password).subscribe({
      next: () => {
        this.busy.set(false);
        const next = this.route.snapshot.queryParamMap.get('next');
        void this.router.navigateByUrl(next && next.startsWith('/') ? next : '/');
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.form.controls.password.reset();
        this.failure.set(toApiError(error));
      },
    });
  }
}
