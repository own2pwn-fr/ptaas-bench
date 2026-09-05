import { Injectable, computed, inject, signal } from '@angular/core';

import { SessionService } from './session.service';

export interface DeskAccount {
  id: string;
  name: string;
}

const STORAGE_KEY = 'mrd.desk.account';

/**
 * Support-desk account switcher.
 *
 * Operators on the desk handle several customer accounts in a shift, so the top bar
 * lets them pick which account the summary report is compiled for. The choice is kept
 * for the browser session only; the API still decides what the operator may read.
 */
@Injectable({ providedIn: 'root' })
export class SupportDeskService {
  private readonly session = inject(SessionService);

  private readonly selectedSignal = signal<string | null>(readStored());
  private readonly accountsSignal = signal<DeskAccount[]>([]);

  readonly accounts = this.accountsSignal.asReadonly();
  readonly selectedAccountId = this.selectedSignal.asReadonly();

  /** True while the desk is looking at an account other than the operator's own. */
  readonly actingForOtherAccount = computed(() => {
    const selected = this.selectedSignal();
    return selected !== null && selected !== this.session.accountId();
  });

  readonly selectedAccountName = computed(() => {
    const selected = this.selectedSignal();
    if (selected === null) {
      return this.session.accountName();
    }
    return this.accountsSignal().find((account) => account.id === selected)?.name ?? selected;
  });

  setAccounts(accounts: DeskAccount[]): void {
    this.accountsSignal.set(accounts);
  }

  select(accountId: string | null): void {
    const ownAccount = this.session.accountId();
    const value = accountId === null || accountId === ownAccount ? null : accountId;
    this.selectedSignal.set(value);
    try {
      if (value === null) {
        window.sessionStorage.removeItem(STORAGE_KEY);
      } else {
        window.sessionStorage.setItem(STORAGE_KEY, value);
      }
    } catch {
      // Private browsing modes refuse storage; the switcher still works in-memory.
    }
  }

  clear(): void {
    this.select(null);
  }
}

function readStored(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}
