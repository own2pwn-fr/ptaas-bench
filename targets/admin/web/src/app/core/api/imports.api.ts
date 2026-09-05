import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { ImportJob } from '../models/domain.model';
import { Page } from '../models/page.model';
import { queryParams } from './http-params.util';

/** Bulk archives loaded by the platform team during onboarding. */
@Injectable({ providedIn: 'root' })
export class ImportsApi {
  private readonly http = inject(HttpClient);

  uploadArchive(file: File): Observable<ImportJob> {
    const form = new FormData();
    form.append('archive', file, file.name);
    return this.http.post<ImportJob>('/api/imports/archives', form);
  }

  history(page = 1): Observable<Page<ImportJob>> {
    return this.http.get<Page<ImportJob>>('/api/imports/history', {
      params: queryParams({ page }),
    });
  }
}
