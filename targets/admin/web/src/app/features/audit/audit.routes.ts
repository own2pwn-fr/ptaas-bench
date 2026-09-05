import { Routes } from '@angular/router';

/**
 * The audit trail.
 *
 * One chunk for the two screens: an operator who opens the trail to answer a customs or
 * insurance query almost always drills into a single event straight afterwards.
 */
export const AUDIT_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () => import('./audit-events.component').then((m) => m.AuditEventsComponent),
    title: 'Audit trail',
  },
  {
    path: ':id',
    loadComponent: () =>
      import('./audit-event-detail.component').then((m) => m.AuditEventDetailComponent),
    title: 'Audit event',
  },
];
