/** The three roles the console understands, weakest first. */
export type Role = 'viewer' | 'analyst' | 'administrator';

/** Ordering used by the route guards; higher wins. */
export const ROLE_RANK: Readonly<Record<Role, number>> = {
  viewer: 0,
  analyst: 1,
  administrator: 2,
};

export interface SessionContext {
  authenticated: boolean;
  subjectId: string | null;
  displayName: string | null;
  role: Role;
  accountId: string | null;
  accountName: string | null;
  /** Optional capabilities toggled per deployment, e.g. `consignment-tracking`. */
  features: string[];
}

export interface LoginResult {
  subjectId: string;
  displayName: string;
  role: Role;
  accountId: string;
  accountName: string;
}

export interface SigningKey {
  kid: string;
  alg: string;
  pem: string;
}

export interface SigningKeySet {
  keys: SigningKey[];
}

/** The context we fall back to before `GET /api/session/context` has answered. */
export const ANONYMOUS_CONTEXT: SessionContext = {
  authenticated: false,
  subjectId: null,
  displayName: null,
  role: 'viewer',
  accountId: null,
  accountName: null,
  features: [],
};
