import { inject } from '@angular/core';
import { CanMatchFn, Router } from '@angular/router';

import { ROLE_RANK, Role } from '../models/session.model';
import { SessionService } from '../services/session.service';

/**
 * Route gate on the operator's role.
 *
 * A signed-out operator is sent to sign in; a signed-in operator who simply lacks the
 * role stays inside the console and gets the "not available to your role" screen, which
 * is far less confusing than bouncing them to a login form they are already past.
 */
export function requireRole(minimum: Role): CanMatchFn {
  return () => {
    const session = inject(SessionService);
    const router = inject(Router);

    if (!session.authenticated()) {
      return router.createUrlTree(['/sign-in'], {
        queryParams: { next: router.getCurrentNavigation()?.extractedUrl.toString() ?? '/' },
      });
    }

    if (ROLE_RANK[session.role()] >= ROLE_RANK[minimum]) {
      return true;
    }

    return router.createUrlTree(['/forbidden']);
  };
}

/** Any signed-in operator, whatever their role. */
export const requireSignedIn: CanMatchFn = requireRole('viewer');
