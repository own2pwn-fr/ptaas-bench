import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** Tab strip shared by the reporting screens. */
@Component({
  selector: 'mrd-report-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="tabs" aria-label="Report sections">
      <a routerLink="/reports" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }"
        >All reports</a
      >
      <a routerLink="/reports/ledger" routerLinkActive="active">Ledger</a>
      <a routerLink="/reports/summary" routerLinkActive="active">Account summary</a>
      <a routerLink="/reports/volumes" routerLinkActive="active">Volumes</a>
    </nav>
  `,
})
export class ReportTabsComponent {}
