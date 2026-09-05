import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { ExportJob, ExportTemplate, RenderResult } from '../models/domain.model';
import { Page } from '../models/page.model';
import { queryParams } from './http-params.util';

/** Document rendering and bulk extracts. */
@Injectable({ providedIn: 'root' })
export class ExportsApi {
  private readonly http = inject(HttpClient);

  templates(): Observable<Page<ExportTemplate>> {
    return this.http.get<Page<ExportTemplate>>('/api/exports/templates');
  }

  /**
   * `stylesheet` is either the name of a stored layout or, for the odd customer that
   * insists on its own paperwork, the layout the operator pasted into the custom box.
   */
  render(statementId: string, stylesheet: string): Observable<RenderResult> {
    return this.http.post<RenderResult>('/api/exports/render', { statementId, stylesheet });
  }

  batch(format: string, rows: number): Observable<ExportJob> {
    return this.http.post<ExportJob>('/api/exports/batch', { format, rows });
  }

  history(page = 1): Observable<Page<ExportJob>> {
    return this.http.get<Page<ExportJob>>('/api/exports/history', {
      params: queryParams({ page }),
    });
  }
}
