import { Routes } from '@angular/router';

/**
 * Inbound documents.
 *
 * One chunk for the three screens: an operator who has just pushed a carrier message
 * through almost always opens the history straight afterwards to confirm the line count.
 */
export const INTAKE_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./document-intake.component').then((m) => m.DocumentIntakeComponent),
    title: 'Carrier message intake',
  },
  {
    path: 'manifests',
    loadComponent: () =>
      import('./manifest-upload.component').then((m) => m.ManifestUploadComponent),
    title: 'Manifest upload',
  },
  {
    path: 'history',
    loadComponent: () =>
      import('./intake-history.component').then((m) => m.IntakeHistoryComponent),
    title: 'Intake history',
  },
];
