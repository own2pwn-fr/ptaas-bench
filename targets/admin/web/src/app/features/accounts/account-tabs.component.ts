import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** Tab strip shared by the four account screens. */
@Component({
  selector: 'mrd-account-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="tabs" aria-label="Account sections">
      <a
        [routerLink]="['/orgs', orgId()]"
        routerLinkActive="active"
        [routerLinkActiveOptions]="{ exact: true }"
        >Overview</a
      >
      <a [routerLink]="['/orgs', orgId(), 'members']" routerLinkActive="active">Members</a>
      <a [routerLink]="['/orgs', orgId(), 'invoices']" routerLinkActive="active">Invoices</a>
      <a [routerLink]="['/orgs', orgId(), 'consignments']" routerLinkActive="active"
        >Consignments</a
      >
    </nav>
  `,
})
export class AccountTabsComponent {
  readonly orgId = input.required<string>();
}
