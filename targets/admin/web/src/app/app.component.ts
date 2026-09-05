import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';

import { AccountsApi } from './core/api/accounts.api';
import { ROLE_RANK, Role } from './core/models/session.model';
import { OperationsContextService } from './core/services/operations-context.service';
import { SessionService } from './core/services/session.service';
import { SupportDeskService } from './core/services/support-desk.service';
import { NoticeBannerComponent } from './features/notices/notice-banner.component';
import { CookieConsentComponent } from './shell/cookie-consent.component';

interface NavItem {
  label: string;
  link: string;
  minRole: Role;
  /** Shown in the side nav under this heading. */
  group: 'Operations' | 'Commercial' | 'Platform';
  hint: string;
}

const NAV: readonly NavItem[] = [
  { label: 'Overview', link: '/', minRole: 'viewer', group: 'Operations', hint: 'Today across the network' },
  { label: 'Accounts', link: '/orgs', minRole: 'viewer', group: 'Commercial', hint: 'Customers and their paperwork' },
  { label: 'Reports', link: '/reports', minRole: 'viewer', group: 'Commercial', hint: 'Ledger, summary and volumes' },
  { label: 'Directory', link: '/directory', minRole: 'viewer', group: 'Operations', hint: 'Who is who in the group' },
  { label: 'Tariffs', link: '/tariffs', minRole: 'analyst', group: 'Commercial', hint: 'Rate cards and bands' },
  { label: 'Intake', link: '/intake', minRole: 'analyst', group: 'Operations', hint: 'Inbound documents and manifests' },
  { label: 'Exports', link: '/exports', minRole: 'analyst', group: 'Operations', hint: 'Rendered documents and extracts' },
  { label: 'Rules', link: '/rules', minRole: 'analyst', group: 'Operations', hint: 'Routing and pricing logic' },
  { label: 'Approvals', link: '/approvals', minRole: 'analyst', group: 'Commercial', hint: 'Decisions waiting on you' },
  { label: 'Notices', link: '/notices', minRole: 'viewer', group: 'Operations', hint: 'Messages shown across the console' },
  { label: 'Notifications', link: '/notifications', minRole: 'analyst', group: 'Operations', hint: 'Outbound customer messages' },
  { label: 'Audit trail', link: '/audit', minRole: 'viewer', group: 'Platform', hint: 'Who did what, and when' },
  { label: 'Imports', link: '/imports', minRole: 'administrator', group: 'Platform', hint: 'Bulk archives' },
  { label: 'Integrations', link: '/integrations', minRole: 'administrator', group: 'Platform', hint: 'Partner connections' },
  { label: 'Settings', link: '/settings', minRole: 'viewer', group: 'Platform', hint: 'Workspace and profile' },
];

/** Application shell: chrome, navigation and the outlet every screen renders into. */
@Component({
  selector: 'mrd-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    RouterLink,
    RouterLinkActive,
    RouterOutlet,
    NoticeBannerComponent,
    CookieConsentComponent,
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent {
  private readonly router = inject(Router);
  private readonly accounts = inject(AccountsApi);

  readonly session = inject(SessionService);
  readonly desk = inject(SupportDeskService);
  readonly operations = inject(OperationsContextService);

  readonly quickSearch = new FormControl('', { nonNullable: true });
  readonly navOpen = signal(true);
  readonly userMenuOpen = signal(false);
  readonly year = new Date().getFullYear();

  /** Chrome is hidden on the two screens an operator can reach signed out. */
  readonly chromeVisible = signal(!isChromeless(window.location.pathname));

  /** Guards the one-off start-up work below against a second pass. */
  private started = false;

  readonly groups = computed(() => {
    const role = this.session.role();
    const allowed = NAV.filter((item) => ROLE_RANK[role] >= ROLE_RANK[item.minRole]);
    const names: Array<NavItem['group']> = ['Operations', 'Commercial', 'Platform'];
    return names
      .map((name) => ({ name, items: allowed.filter((item) => item.group === name) }))
      .filter((group) => group.items.length > 0);
  });

  readonly canSwitchAccount = computed(() => this.session.hasRole('analyst'));

  constructor() {
    this.router.events.pipe(filter((event) => event instanceof NavigationEnd)).subscribe((event) => {
      const url = (event as NavigationEnd).urlAfterRedirects;
      this.chromeVisible.set(!isChromeless(url));
      this.userMenuOpen.set(false);
    });

    // The desk switcher and the badge counters only make sense once we know who is
    // signed in, and the session resolves during start-up.
    effect(() => {
      if (!this.session.authenticated() || this.started) {
        return;
      }
      this.started = true;
      this.operations.refresh();
      if (this.canSwitchAccount()) {
        this.loadDeskAccounts();
      }
    });
  }

  submitSearch(): void {
    const term = this.quickSearch.value.trim();
    if (term === '') {
      return;
    }
    void this.router.navigate(['/search'], { queryParams: { q: term } });
  }

  onDeskChange(value: string): void {
    this.desk.select(value === '' ? null : value);
  }

  toggleNav(): void {
    this.navOpen.update((open) => !open);
  }

  toggleUserMenu(): void {
    this.userMenuOpen.update((open) => !open);
  }

  signOut(): void {
    this.session.logout().subscribe({
      next: () => void this.router.navigate(['/sign-in']),
      error: () => void this.router.navigate(['/sign-in']),
    });
  }

  private loadDeskAccounts(): void {
    this.accounts.list({ page: 1, size: 50 }).subscribe({
      next: (page) =>
        this.desk.setAccounts(page.items.map((account) => ({ id: account.id, name: account.name }))),
      error: () => this.desk.setAccounts([]),
    });
  }
}

/** The sign-in and recovery screens are shown without the console chrome. */
function isChromeless(url: string): boolean {
  return url.startsWith('/sign-in') || url.startsWith('/account-recovery');
}
