import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, catchError, of, tap } from 'rxjs';

import {
  ANONYMOUS_CONTEXT,
  LoginResult,
  ROLE_RANK,
  Role,
  SessionContext,
  SigningKeySet,
} from '../models/session.model';

/**
 * Holds who the console is talking to.
 *
 * The API keeps the session in an HttpOnly cookie (`mrd_session`), so the front end
 * has no way to inspect it directly and asks `/api/session/context` on boot instead.
 */
@Injectable({ providedIn: 'root' })
export class SessionService {
  private readonly http = inject(HttpClient);

  private readonly contextSignal = signal<SessionContext>(ANONYMOUS_CONTEXT);
  private readonly loadedSignal = signal(false);

  readonly context = this.contextSignal.asReadonly();
  readonly loaded = this.loadedSignal.asReadonly();
  readonly authenticated = computed(() => this.contextSignal().authenticated);
  readonly role = computed<Role>(() => this.contextSignal().role);
  readonly displayName = computed(() => this.contextSignal().displayName ?? 'Signed out');
  readonly accountId = computed(() => this.contextSignal().accountId);
  readonly accountName = computed(() => this.contextSignal().accountName ?? '');
  readonly initials = computed(() => {
    const name = this.contextSignal().displayName ?? '';
    const parts = name.replace(/[^\p{L}\s.]/gu, '').split(/[\s.]+/).filter(Boolean);
    return parts.slice(0, 2).map((part) => part[0]?.toUpperCase() ?? '').join('') || '—';
  });

  /** Resolved once during application start-up so guards can read it synchronously. */
  load(): Observable<SessionContext> {
    return this.http.get<SessionContext>('/api/session/context').pipe(
      catchError(() => of(ANONYMOUS_CONTEXT)),
      tap((context) => {
        this.contextSignal.set(normalise(context));
        this.loadedSignal.set(true);
      }),
    );
  }

  login(email: string, password: string): Observable<LoginResult> {
    return this.http.post<LoginResult>('/api/auth/login', { email, password }).pipe(
      tap((result) => {
        this.contextSignal.set(
          normalise({
            authenticated: true,
            subjectId: result.subjectId,
            displayName: result.displayName,
            role: result.role,
            accountId: result.accountId,
            accountName: result.accountName,
            features: this.contextSignal().features,
          }),
        );
        this.loadedSignal.set(true);
      }),
    );
  }

  logout(): Observable<void> {
    return this.http.post<void>('/api/auth/logout', {}).pipe(
      tap(() => {
        this.contextSignal.set(ANONYMOUS_CONTEXT);
        this.loadedSignal.set(true);
      }),
    );
  }

  /**
   * Recovery is answered identically whether or not the reference matched, so the
   * screen only ever shows the same confirmation.
   */
  recover(reference: string): Observable<{ accepted: boolean }> {
    return this.http.post<{ accepted: boolean }>('/api/auth/recover', { reference });
  }

  /** Verification keys the group's other services use to check console tokens. */
  signingKeys(): Observable<SigningKeySet> {
    return this.http.get<SigningKeySet>('/api/auth/keys');
  }

  hasRole(minimum: Role): boolean {
    return ROLE_RANK[this.role()] >= ROLE_RANK[minimum];
  }

  hasFeature(name: string): boolean {
    return this.contextSignal().features.includes(name);
  }
}

function normalise(context: SessionContext): SessionContext {
  return {
    ...context,
    role: context.role ?? 'viewer',
    features: Array.isArray(context.features) ? context.features : [],
  };
}
