import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { AuditEvent } from '../models/domain.model';
import { Page } from '../models/page.model';
import { queryParams } from './http-params.util';

/** The audit trail, kept for seven years for customs and insurance. */
@Injectable({ providedIn: 'root' })
export class AuditApi {
  private readonly http = inject(HttpClient);

  events(
    options: { actor?: string; action?: string; expand?: string; page?: number } = {},
  ): Observable<Page<AuditEvent>> {
    return this.http.get<Page<AuditEvent>>('/api/audit/events', {
      params: queryParams({
        actor: options.actor,
        action: options.action,
        expand: options.expand,
        page: options.page,
      }),
    });
  }

  event(id: string): Observable<AuditEvent> {
    return this.http.get<AuditEvent>(`/api/audit/events/${id}`);
  }
}
