import { Routes } from '@angular/router';

/**
 * Reporting.
 *
 * One chunk for the three reports: finance open the landing page at quarter end and
 * usually run the ledger and the volumes report back to back.
 */
export const REPORTS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () => import('./reports-home.component').then((m) => m.ReportsHomeComponent),
    title: 'Reports',
  },
  {
    path: 'ledger',
    loadComponent: () => import('./ledger-report.component').then((m) => m.LedgerReportComponent),
    title: 'Ledger report',
  },
  {
    path: 'summary',
    loadComponent: () => import('./summary-report.component').then((m) => m.SummaryReportComponent),
    title: 'Account summary',
  },
  {
    path: 'volumes',
    loadComponent: () => import('./volumes-report.component').then((m) => m.VolumesReportComponent),
    title: 'Volumes report',
  },
];
