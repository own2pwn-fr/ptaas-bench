import { Routes } from '@angular/router';

/**
 * Partner connections.
 *
 * One chunk for the three screens: whoever opens the connection list is usually on the
 * phone with a partner and moves straight on to a delivery check or a credential.
 * The area is already restricted to administrators by the parent route.
 */
export const INTEGRATIONS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./integrations-list.component').then((m) => m.IntegrationsListComponent),
    title: 'Partner connections',
  },
  {
    path: 'webhooks',
    loadComponent: () => import('./webhook-probe.component').then((m) => m.WebhookProbeComponent),
    title: 'Delivery check',
  },
  {
    path: 'credentials',
    loadComponent: () =>
      import('./credential-store.component').then((m) => m.CredentialStoreComponent),
    title: 'Partner credentials',
  },
];
