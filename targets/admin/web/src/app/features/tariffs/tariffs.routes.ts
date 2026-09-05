import { Routes } from '@angular/router';

/**
 * Rate cards.
 *
 * The card and the band lookup ship together: a quotation clerk moves between them
 * while pricing a single consignment.
 */
export const TARIFFS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () => import('./tariffs-home.component').then((m) => m.TariffsHomeComponent),
    title: 'Tariffs',
  },
  {
    path: 'bands',
    loadComponent: () => import('./tariff-bands.component').then((m) => m.TariffBandsComponent),
    title: 'Band lookup',
  },
];
