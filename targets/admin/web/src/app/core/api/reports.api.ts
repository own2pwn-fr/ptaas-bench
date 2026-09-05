import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { LedgerReport, SummaryReport, VolumeReport } from '../models/domain.model';
import { queryParams } from './http-params.util';

/** Reporting endpoints behind the three report screens. */
@Injectable({ providedIn: 'root' })
export class ReportsApi {
  private readonly http = inject(HttpClient);

  /**
   * `window` accepts the presets offered by the picker as well as a period the operator
   * types themselves — finance ask for windows the presets do not cover at quarter end.
   */
  ledger(window: string, account?: string | null): Observable<LedgerReport> {
    return this.http.get<LedgerReport>('/api/reports/ledger', {
      params: queryParams({ window, account }),
    });
  }

  /** Compiled for the account the support desk is currently handling. */
  summary(): Observable<SummaryReport> {
    return this.http.get<SummaryReport>('/api/reports/summary');
  }

  volumes(from: string, to: string, granularity: string): Observable<VolumeReport> {
    return this.http.get<VolumeReport>('/api/reports/volumes', {
      params: queryParams({ from, to, granularity }),
    });
  }
}
