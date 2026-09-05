import { Routes } from '@angular/router';

/**
 * Staff directory.
 *
 * Two screens in one chunk: the list is nearly always opened to reach one person's card.
 */
export const DIRECTORY_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () => import('./people-list.component').then((m) => m.PeopleListComponent),
    title: 'Directory',
  },
  {
    path: ':uid',
    loadComponent: () => import('./person-detail.component').then((m) => m.PersonDetailComponent),
    title: 'Directory entry',
  },
];
