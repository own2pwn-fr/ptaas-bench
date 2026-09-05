import { Routes } from '@angular/router';

/**
 * Outbound paperwork: rendered statements and bulk extracts.
 *
 * One chunk, because the render screen sends an operator to the stored layouts and back
 * again while they work out which one a customer expects.
 */
export const EXPORTS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./document-render.component').then((m) => m.DocumentRenderComponent),
    title: 'Render and extract',
  },
  {
    path: 'templates',
    loadComponent: () =>
      import('./export-templates.component').then((m) => m.ExportTemplatesComponent),
    title: 'Stored layouts',
  },
  {
    path: 'history',
    loadComponent: () =>
      import('./export-history.component').then((m) => m.ExportHistoryComponent),
    title: 'Export history',
  },
];
