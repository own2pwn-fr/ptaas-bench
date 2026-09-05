import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { TariffBand } from '../models/domain.model';
import { Page } from '../models/page.model';
import { queryParams } from './http-params.util';

/** Rate cards used when quoting a consignment. */
@Injectable({ providedIn: 'root' })
export class TariffsApi {
  private readonly http = inject(HttpClient);

  list(): Observable<Page<TariffBand>> {
    return this.http.get<Page<TariffBand>>('/api/tariffs');
  }

  lookup(band: string): Observable<TariffBand[]> {
    return this.http.get<TariffBand[]>('/api/tariffs/lookup', {
      params: queryParams({ band }),
    });
  }
}
