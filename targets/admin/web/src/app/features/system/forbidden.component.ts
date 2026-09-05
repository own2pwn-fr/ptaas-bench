import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { SessionService } from '../../core/services/session.service';

/** Shown when a signed-in operator opens an area their role does not cover. */
@Component({
  selector: 'mrd-forbidden',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  template: `
    <div class="page status">
      <p class="status__code">403</p>
      <h1>That area is not part of your role</h1>
      <p>
        You are signed in as <strong>{{ session.displayName() }}</strong> with the
        <strong>{{ session.context().role }}</strong> role, which does not cover this part of
        Meridian.
      </p>
      <p class="muted small">
        Access is granted per team by the platform group. Ask your line manager to raise a
        request, or call the service desk on extension 4120 if you believe this is wrong.
      </p>
      <div class="status__actions">
        <a class="btn btn--primary" routerLink="/">Back to the overview</a>
        <a class="btn" routerLink="/directory">Find your line manager</a>
      </div>
    </div>
  `,
  styleUrl: './status.scss',
})
export class ForbiddenComponent {
  readonly session = inject(SessionService);
}
