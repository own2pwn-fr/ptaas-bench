import { Routes } from '@angular/router';

/**
 * Outbound notifications.
 *
 * One chunk for the three screens: an operator who renders a message almost always goes
 * on to check the template it came from, or the log entry it produced.
 */
export const NOTIFICATIONS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./notifications-home.component').then((m) => m.NotificationsHomeComponent),
    title: 'Notifications',
  },
  {
    path: 'templates',
    loadComponent: () =>
      import('./notification-templates.component').then((m) => m.NotificationTemplatesComponent),
    title: 'Notification templates',
  },
  {
    path: 'log',
    loadComponent: () =>
      import('./notification-log.component').then((m) => m.NotificationLogComponent),
    title: 'Notification log',
  },
];
