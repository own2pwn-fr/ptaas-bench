import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { IntakeDocument, IntakeReceipt } from '../models/domain.model';
import { Page } from '../models/page.model';
import { queryParams } from './http-params.util';

/** Inbound documents: carrier messages, manifests, and what became of them. */
@Injectable({ providedIn: 'root' })
export class IntakeApi {
  private readonly http = inject(HttpClient);

  /**
   * Carrier messages arrive as XML documents. Operators paste one into the console when
   * a partner's automated feed has failed, so the body is posted verbatim.
   */
  submitDocument(document: string): Observable<IntakeReceipt> {
    return this.http.post<IntakeReceipt>('/api/intake/documents', document, {
      headers: { 'Content-Type': 'application/xml' },
    });
  }

  uploadManifest(file: File): Observable<IntakeReceipt> {
    const form = new FormData();
    form.append('manifest', file, file.name);
    return this.http.post<IntakeReceipt>('/api/intake/manifests', form);
  }

  history(page = 1): Observable<Page<IntakeDocument>> {
    return this.http.get<Page<IntakeDocument>>('/api/intake/history', {
      params: queryParams({ page }),
    });
  }
}
