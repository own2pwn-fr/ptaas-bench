import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { Integration, ProbeResult } from '../models/domain.model';
import { Page } from '../models/page.model';

/** Partner connections: EDI links, webhooks, SFTP drops. */
@Injectable({ providedIn: 'root' })
export class IntegrationsApi {
  private readonly http = inject(HttpClient);

  list(): Observable<Page<Integration>> {
    return this.http.get<Page<Integration>>('/api/integrations');
  }

  create(integration: Partial<Integration>): Observable<Integration> {
    return this.http.post<Integration>('/api/integrations', integration);
  }

  /** Calls a partner endpoint once and shows what came back, for setup support. */
  probeWebhook(endpoint: string): Observable<ProbeResult> {
    return this.http.post<ProbeResult>('/api/integrations/webhooks/probe', { endpoint });
  }

  storeCredential(integrationId: string, secret: string): Observable<{ stored: boolean }> {
    return this.http.post<{ stored: boolean }>('/api/integrations/credentials', {
      integrationId,
      secret,
    });
  }
}
