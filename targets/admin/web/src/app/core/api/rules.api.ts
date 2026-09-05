import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { RulePreviewResult, RuleSummary } from '../models/domain.model';
import { Page } from '../models/page.model';

/** Routing and pricing rules the operations engine applies to every booking. */
@Injectable({ providedIn: 'root' })
export class RulesApi {
  private readonly http = inject(HttpClient);

  list(): Observable<Page<RuleSummary>> {
    return this.http.get<Page<RuleSummary>>('/api/rules');
  }

  get(id: string): Observable<RuleSummary> {
    return this.http.get<RuleSummary>(`/api/rules/${id}`);
  }

  create(rule: Partial<RuleSummary>): Observable<RuleSummary> {
    return this.http.post<RuleSummary>('/api/rules', rule);
  }

  /** Runs an expression against a sample record so the author can see the outcome. */
  preview(expression: string, sample: string): Observable<RulePreviewResult> {
    return this.http.post<RulePreviewResult>('/api/rules/preview', { expression, sample });
  }
}
