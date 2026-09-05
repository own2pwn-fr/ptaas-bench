import { Routes } from '@angular/router';

/**
 * Approvals: credit limit raises, rate overrides and write-offs waiting on a decision.
 *
 * One chunk for both screens — an operator who opens the register always opens at
 * least one request from it.
 */
export const APPROVALS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./approvals-list.component').then((m) => m.ApprovalsListComponent),
    title: 'Approvals',
  },
  {
    path: ':id',
    loadComponent: () =>
      import('./approval-detail.component').then((m) => m.ApprovalDetailComponent),
    title: 'Approval request',
  },
];
