import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** Tab strip shared by the three intake screens. */
@Component({
  selector: 'mrd-intake-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="tabs" aria-label="Intake sections">
      <a routerLink="/intake" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }"
        >Carrier message</a
      >
      <a routerLink="/intake/manifests" routerLinkActive="active">Manifest upload</a>
      <a routerLink="/intake/history" routerLinkActive="active">History</a>
    </nav>
  `,
})
export class IntakeTabsComponent {}
