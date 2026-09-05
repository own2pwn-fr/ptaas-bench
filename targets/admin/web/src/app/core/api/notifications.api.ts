import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  NotificationLogEntry,
  NotificationPreview,
  NotificationTemplate,
} from '../models/domain.model';
import { Page } from '../models/page.model';
import { queryParams } from './http-params.util';

/** Outbound messages to customers and staff. */
@Injectable({ providedIn: 'root' })
export class NotificationsApi {
  private readonly http = inject(HttpClient);

  templates(): Observable<Page<NotificationTemplate>> {
    return this.http.get<Page<NotificationTemplate>>('/api/notifications/templates');
  }

  preview(template: string, sample: string): Observable<NotificationPreview> {
    return this.http.post<NotificationPreview>('/api/notifications/preview', {
      template,
      sample,
    });
  }

  log(page = 1): Observable<Page<NotificationLogEntry>> {
    return this.http.get<Page<NotificationLogEntry>>('/api/notifications/log', {
      params: queryParams({ page }),
    });
  }
}
