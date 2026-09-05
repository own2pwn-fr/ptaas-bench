import { Routes } from '@angular/router';

import { requireRole } from '../../core/guards/role.guard';

/**
 * Console settings.
 *
 * The landing page and the operator's own profile are open to everyone who can sign in.
 * The saved layout is an analyst screen: viewers work from the standard arrangement the
 * branch offices agreed on.
 */
export const SETTINGS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () => import('./settings-home.component').then((m) => m.SettingsHomeComponent),
    title: 'Settings',
  },
  {
    path: 'workspace',
    canMatch: [requireRole('analyst')],
    loadComponent: () =>
      import('./workspace-settings.component').then((m) => m.WorkspaceSettingsComponent),
    title: 'Console layout',
  },
  {
    path: 'profile',
    loadComponent: () =>
      import('./profile-settings.component').then((m) => m.ProfileSettingsComponent),
    title: 'Your profile',
  },
];
