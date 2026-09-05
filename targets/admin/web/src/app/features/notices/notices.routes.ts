import { Routes } from '@angular/router';

import { requireRole } from '../../core/guards/role.guard';

/**
 * Operations notices.
 *
 * Reading the board is open to anyone signed in — the banner already puts the current
 * notices on every screen — so the parent route only asks for `viewer`. Writing one is
 * gated on its own: a notice goes out to every desk in the group at once.
 */
export const NOTICES_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () => import('./notices-list.component').then((m) => m.NoticesListComponent),
    title: 'Service notices',
  },
  {
    path: 'new',
    canMatch: [requireRole('analyst')],
    loadComponent: () => import('./notice-editor.component').then((m) => m.NoticeEditorComponent),
    title: 'Post a notice',
  },
];
