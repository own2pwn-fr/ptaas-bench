import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { Notice } from '../models/domain.model';

export interface NoticeDraft {
  title: string;
  body: string;
  severity: string;
  publishedFrom: string;
  publishedTo: string | null;
}

/** Operations notices shown in the banner across the console. */
@Injectable({ providedIn: 'root' })
export class NoticesApi {
  private readonly http = inject(HttpClient);

  list(): Observable<Notice[]> {
    return this.http.get<Notice[]>('/api/notices');
  }

  publish(draft: NoticeDraft): Observable<Notice> {
    return this.http.post<Notice>('/api/notices', draft);
  }
}
