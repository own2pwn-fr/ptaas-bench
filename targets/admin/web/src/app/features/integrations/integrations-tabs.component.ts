import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** Tab strip shared by the three partner-connection screens. */
@Component({
  selector: 'mrd-integrations-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="tabs" aria-label="Partner connection sections">
      <a
        routerLink="/integrations"
        routerLinkActive="active"
        [routerLinkActiveOptions]="{ exact: true }"
        >Connections</a
      >
      <a routerLink="/integrations/webhooks" routerLinkActive="active">Delivery check</a>
      <a routerLink="/integrations/credentials" routerLinkActive="active">Credentials</a>
    </nav>
  `,
})
export class IntegrationsTabsComponent {}
