import { Routes } from '@angular/router';

/**
 * Routing and pricing rules.
 *
 * `preview` is declared before `:id` so the preview screen keeps its own URL: the parameter
 * route would otherwise match it first and ask the API for a rule called "preview".
 */
export const RULES_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () => import('./rules-list.component').then((m) => m.RulesListComponent),
    title: 'Rules',
  },
  {
    path: 'preview',
    loadComponent: () => import('./rule-preview.component').then((m) => m.RulePreviewComponent),
    title: 'Expression preview',
  },
  {
    path: ':id',
    loadComponent: () => import('./rule-detail.component').then((m) => m.RuleDetailComponent),
    title: 'Rule',
  },
];
