import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';

import { SupportDeskService } from '../services/support-desk.service';

/** Endpoints compiled for whichever account the support desk is currently handling. */
const DESK_SCOPED_ENDPOINTS = ['/api/reports/summary'];

/**
 * Support-desk account context.
 *
 * When a desk operator picks a customer account other than their own in the top bar,
 * the summary report has to be compiled for that account rather than for the operator's.
 * The choice travels as a header so the report URL stays shareable between colleagues.
 * With the default selection — the operator's own account — nothing is added.
 */
export const accountContextInterceptor: HttpInterceptorFn = (request, next) => {
  const desk = inject(SupportDeskService);
  const accountId = desk.selectedAccountId();

  if (!desk.actingForOtherAccount() || accountId === null) {
    return next(request);
  }

  const path = request.url.split('?')[0];
  if (!DESK_SCOPED_ENDPOINTS.includes(path)) {
    return next(request);
  }

  return next(request.clone({ setHeaders: { 'X-Account-Context': accountId } }));
};
