import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { Approval } from '../models/domain.model';
import { Page } from '../models/page.model';
import { queryParams } from './http-params.util';

/** Requests waiting on a human decision. */
@Injectable({ providedIn: 'root' })
export class ApprovalsApi {
  private readonly http = inject(HttpClient);

  list(options: { state?: string; page?: number } = {}): Observable<Page<Approval>> {
    return this.http.get<Page<Approval>>('/api/approvals', {
      params: queryParams({ state: options.state, page: options.page }),
    });
  }

  get(id: string): Observable<Approval> {
    return this.http.get<Approval>(`/api/approvals/${id}`);
  }

  decide(id: string, decision: 'approve' | 'reject', note: string): Observable<Approval> {
    return this.http.post<Approval>(`/api/approvals/${id}/decision`, { decision, note });
  }
}
