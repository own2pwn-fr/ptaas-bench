import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** Tab strip shared by the three notification screens. */
@Component({
  selector: 'mrd-notifications-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="tabs" aria-label="Notification sections">
      <a
        routerLink="/notifications"
        routerLinkActive="active"
        [routerLinkActiveOptions]="{ exact: true }"
        >Preview</a
      >
      <a routerLink="/notifications/templates" routerLinkActive="active">Templates</a>
      <a routerLink="/notifications/log" routerLinkActive="active">Delivery log</a>
    </nav>
  `,
})
export class NotificationsTabsComponent {}
