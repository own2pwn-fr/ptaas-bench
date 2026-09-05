import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

/**
 * Sends the operator back to the sign-in screen when the session cookie has lapsed,
 * keeping the page they were on so they land back on it afterwards. Everything else
 * is left to the feature that made the call.
 */
export const sessionExpiryInterceptor: HttpInterceptorFn = (request, next) => {
  const router = inject(Router);

  return next(request).pipe(
    catchError((error: unknown) => {
      const isAuthCall = request.url.startsWith('/api/auth/');
      if (error instanceof HttpErrorResponse && error.status === 401 && !isAuthCall) {
        void router.navigate(['/sign-in'], {
          queryParams: { next: router.url },
          replaceUrl: true,
        });
      }
      return throwError(() => error);
    }),
  );
};
