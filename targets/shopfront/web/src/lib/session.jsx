/**
 * Who is signed in.
 *
 * The session cookie is the source of truth; this context only mirrors what
 * `GET /api/auth/session` last said, so that the header, the account area and the
 * checkout do not each ask the same question on every render.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api } from "./api.js";

const SessionContext = createContext(null);

export function SessionProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await api.get("/api/auth/session");
      const next = data?.user ?? data?.session?.user ?? (data?.authenticated ? data : null);
      setUser(next ?? null);
      return next ?? null;
    } catch {
      setUser(null);
      return null;
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const signIn = useCallback(
    async (email, password) => {
      const data = await api.post("/api/auth/login", { email, password });
      const next = data?.user ?? null;
      if (next) setUser(next);
      else await refresh();
      return data;
    },
    [refresh],
  );

  const register = useCallback(
    async (form) => {
      const data = await api.post("/api/auth/register", form);
      await refresh();
      return data;
    },
    [refresh],
  );

  const signOut = useCallback(async () => {
    try {
      await api.post("/api/auth/logout", {});
    } finally {
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, ready, signedIn: Boolean(user), refresh, signIn, signOut, register }),
    [user, ready, refresh, signIn, signOut, register],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
