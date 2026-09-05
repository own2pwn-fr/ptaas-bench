import { Routes } from '@angular/router';

import { requireRole, requireSignedIn } from './core/guards/role.guard';
import { AccountRecoveryComponent } from './features/auth/account-recovery.component';
import { SignInComponent } from './features/auth/sign-in.component';
import { DashboardComponent } from './features/dashboard/dashboard.component';
import { SearchResultsComponent } from './features/search/search-results.component';
import { ForbiddenComponent } from './features/system/forbidden.component';
import { NotFoundComponent } from './features/system/not-found.component';

/**
 * Route table.
 *
 * The overview, sign-in and the two status screens ship in the main bundle because an
 * operator hits them on every visit. Everything else is one lazy chunk per area, which
 * is what keeps the first paint of the console under a second on a branch office line.
 */
export const APP_ROUTES: Routes = [
  {
    path: '',
    pathMatch: 'full',
    component: DashboardComponent,
    canMatch: [requireSignedIn],
    title: 'Overview',
  },
  {
    path: 'sign-in',
    component: SignInComponent,
    title: 'Sign in',
  },
  {
    path: 'account-recovery',
    component: AccountRecoveryComponent,
    title: 'Account recovery',
  },
  {
    path: 'search',
    component: SearchResultsComponent,
    canMatch: [requireSignedIn],
    title: 'Search',
  },
  {
    path: 'forbidden',
    component: ForbiddenComponent,
    title: 'Not available to your role',
  },
  {
    path: 'orgs',
    canMatch: [requireRole('viewer')],
    loadChildren: () => import('./features/accounts/accounts.routes').then((m) => m.ACCOUNTS_ROUTES),
  },
  {
    path: 'reports',
    canMatch: [requireRole('viewer')],
    loadChildren: () => import('./features/reports/reports.routes').then((m) => m.REPORTS_ROUTES),
  },
  {
    path: 'directory',
    canMatch: [requireRole('viewer')],
    loadChildren: () =>
      import('./features/directory/directory.routes').then((m) => m.DIRECTORY_ROUTES),
  },
  {
    path: 'tariffs',
    canMatch: [requireRole('analyst')],
    loadChildren: () => import('./features/tariffs/tariffs.routes').then((m) => m.TARIFFS_ROUTES),
  },
  {
    path: 'intake',
    canMatch: [requireRole('analyst')],
    loadChildren: () => import('./features/intake/intake.routes').then((m) => m.INTAKE_ROUTES),
  },
  {
    path: 'exports',
    canMatch: [requireRole('analyst')],
    loadChildren: () => import('./features/exports/exports.routes').then((m) => m.EXPORTS_ROUTES),
  },
  {
    path: 'rules',
    canMatch: [requireRole('analyst')],
    loadChildren: () => import('./features/rules/rules.routes').then((m) => m.RULES_ROUTES),
  },
  {
    path: 'approvals',
    canMatch: [requireRole('analyst')],
    loadChildren: () =>
      import('./features/approvals/approvals.routes').then((m) => m.APPROVALS_ROUTES),
  },
  {
    path: 'notices',
    canMatch: [requireRole('viewer')],
    loadChildren: () => import('./features/notices/notices.routes').then((m) => m.NOTICES_ROUTES),
  },
  {
    path: 'notifications',
    canMatch: [requireRole('analyst')],
    loadChildren: () =>
      import('./features/notifications/notifications.routes').then((m) => m.NOTIFICATIONS_ROUTES),
  },
  {
    path: 'audit',
    canMatch: [requireRole('viewer')],
    loadChildren: () => import('./features/audit/audit.routes').then((m) => m.AUDIT_ROUTES),
  },
  {
    path: 'imports',
    canMatch: [requireRole('administrator')],
    loadChildren: () => import('./features/imports/imports.routes').then((m) => m.IMPORTS_ROUTES),
  },
  {
    path: 'integrations',
    canMatch: [requireRole('administrator')],
    loadChildren: () =>
      import('./features/integrations/integrations.routes').then((m) => m.INTEGRATIONS_ROUTES),
  },
  {
    path: 'settings',
    canMatch: [requireRole('viewer')],
    loadChildren: () => import('./features/settings/settings.routes').then((m) => m.SETTINGS_ROUTES),
  },
  {
    path: '**',
    component: NotFoundComponent,
    title: 'Page not found',
  },
];
