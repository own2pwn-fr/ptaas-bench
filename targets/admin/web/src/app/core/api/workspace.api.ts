import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { Profile, WorkspaceLayout } from '../models/domain.model';

/** Per-operator console preferences. */
@Injectable({ providedIn: 'root' })
export class WorkspaceApi {
  private readonly http = inject(HttpClient);

  /** Falls back to the `mrd_layout` cookie for operators without a session yet. */
  layout(): Observable<WorkspaceLayout> {
    return this.http.get<WorkspaceLayout>('/api/workspace/layout');
  }

  saveLayout(state: string): Observable<WorkspaceLayout> {
    return this.http.post<WorkspaceLayout>('/api/workspace/layout', { state });
  }

  /** Applies a layout an operator copied from another workstation. */
  restoreLayout(state: string): Observable<WorkspaceLayout> {
    return this.http.post<WorkspaceLayout>('/api/workspace/layout/restore', { state });
  }

  profile(): Observable<Profile> {
    return this.http.get<Profile>('/api/profile');
  }

  updateProfile(changes: Partial<Profile>): Observable<Profile> {
    return this.http.patch<Profile>('/api/profile', changes);
  }
}
