import { Routes } from '@angular/router';

/**
 * Bulk archive loading.
 *
 * The area is reached only by the platform team, so it is one small lazy chunk that the
 * rest of the console never pays for. The role is already checked on the parent route.
 */
export const IMPORTS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./archive-upload.component').then((m) => m.ArchiveUploadComponent),
    title: 'Load an archive',
  },
  {
    path: 'history',
    loadComponent: () =>
      import('./imports-history.component').then((m) => m.ImportsHistoryComponent),
    title: 'Archive history',
  },
];
