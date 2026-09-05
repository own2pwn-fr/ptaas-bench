import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  Account,
  AccountMember,
  Consignment,
  Invoice,
} from '../models/domain.model';
import { Page } from '../models/page.model';
import { queryParams } from './http-params.util';

/** Customer accounts, their people, their paperwork. */
@Injectable({ providedIn: 'root' })
export class AccountsApi {
  private readonly http = inject(HttpClient);

  list(options: { page?: number; size?: number; q?: string } = {}): Observable<Page<Account>> {
    return this.http.get<Page<Account>>('/api/orgs', {
      params: queryParams({ page: options.page, size: options.size, q: options.q }),
    });
  }

  get(orgId: string): Observable<Account> {
    return this.http.get<Account>(`/api/orgs/${orgId}`);
  }

  /**
   * Members grid.
   *
   * The grid has a column picker, and the columns the operator keeps are the columns we
   * ask the API for — pulling every attribute of every member for a grid that shows
   * three of them was the slowest call in the console.
   */
  members(orgId: string, fields = 'id,displayName,role'): Observable<Page<AccountMember>> {
    return this.http.get<Page<AccountMember>>(`/api/orgs/${orgId}/members`, {
      params: queryParams({ fields }),
    });
  }

  invoices(
    orgId: string,
    options: { status?: string; page?: number } = {},
  ): Observable<Page<Invoice>> {
    return this.http.get<Page<Invoice>>(`/api/orgs/${orgId}/invoices`, {
      params: queryParams({ status: options.status, page: options.page }),
    });
  }

  consignments(
    orgId: string,
    options: { status?: string; page?: number } = {},
  ): Observable<Page<Consignment>> {
    return this.http.get<Page<Consignment>>(`/api/orgs/${orgId}/consignments`, {
      params: queryParams({ status: options.status, page: options.page }),
    });
  }
}
