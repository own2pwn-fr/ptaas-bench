import { Routes } from '@angular/router';

/**
 * Customer accounts.
 *
 * Loaded as one chunk: an operator who opens an account almost always goes on to its
 * members, invoices or consignments in the same sitting.
 */
export const ACCOUNTS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () => import('./account-list.component').then((m) => m.AccountListComponent),
    title: 'Accounts',
  },
  {
    path: ':orgId',
    loadComponent: () => import('./account-detail.component').then((m) => m.AccountDetailComponent),
    title: 'Account',
  },
  {
    path: ':orgId/members',
    loadComponent: () =>
      import('./account-members.component').then((m) => m.AccountMembersComponent),
    title: 'Account members',
  },
  {
    path: ':orgId/invoices',
    loadComponent: () =>
      import('./account-invoices.component').then((m) => m.AccountInvoicesComponent),
    title: 'Account invoices',
  },
  {
    path: ':orgId/consignments',
    loadComponent: () =>
      import('./account-consignments.component').then((m) => m.AccountConsignmentsComponent),
    title: 'Account consignments',
  },
];
