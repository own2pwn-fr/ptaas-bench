import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** Tab strip shared by the three export screens. */
@Component({
  selector: 'mrd-exports-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="tabs" aria-label="Export sections">
      <a routerLink="/exports" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }"
        >Render</a
      >
      <a routerLink="/exports/templates" routerLinkActive="active">Stored layouts</a>
      <a routerLink="/exports/history" routerLinkActive="active">History</a>
    </nav>
  `,
})
export class ExportsTabsComponent {}
