import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { SearchHit } from '../models/domain.model';
import { Page } from '../models/page.model';
import { queryParams } from './http-params.util';

/** Cross-console search over accounts, consignments, invoices and people. */
@Injectable({ providedIn: 'root' })
export class SearchApi {
  private readonly http = inject(HttpClient);

  search(q: string, sort: string, page: number): Observable<Page<SearchHit>> {
    return this.http.get<Page<SearchHit>>('/api/search', {
      params: queryParams({ q, sort, page }),
    });
  }
}
