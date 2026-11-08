import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, getToken, setToken, setUnauthorizedHandler } from "./api";
import type { Allowance, PublicConfig, User } from "./types";

interface AuthPayload {
  token: string;
  user: User;
  allowance: Allowance;
}

interface SessionValue {
  user: User | null;
  allowance: Allowance | null;
  config: PublicConfig | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, name: string) => Promise<void>;
  signOut: () => void;
  refresh: () => Promise<void>;
  apply: (data: { user?: User; allowance?: Allowance }) => void;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [allowance, setAllowance] = useState<Allowance | null>(null);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [loading, setLoading] = useState(true);

  const signOut = useCallback(() => {
    setToken(null);
    setUser(null);
    setAllowance(null);
  }, []);

  const refresh = useCallback(async () => {
    if (!getToken()) return;
    const me = await api<{ user: User; allowance: Allowance }>("/api/auth/me");
    setUser(me.user);
    setAllowance(me.allowance);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(signOut);
    api<PublicConfig>("/api/config").then(setConfig).catch(() => {});
    if (!getToken()) {
      setLoading(false);
      return;
    }
    refresh()
      .catch(() => signOut())
      .finally(() => setLoading(false));
  }, [refresh, signOut]);

  const accept = (p: AuthPayload) => {
    setToken(p.token);
    setUser(p.user);
    setAllowance(p.allowance);
  };

  const value = useMemo<SessionValue>(
    () => ({
      user,
      allowance,
      config,
      loading,
      signIn: async (email, password) => accept(await api<AuthPayload>("/api/auth/login", { body: { email, password } })),
      signUp: async (email, password, name) =>
        accept(await api<AuthPayload>("/api/auth/register", { body: { email, password, name } })),
      signOut,
      refresh,
      apply: ({ user: u, allowance: a }) => {
        if (u) setUser(u);
        if (a) setAllowance(a);
      },
    }),
    [user, allowance, config, loading, signOut, refresh],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const v = useContext(SessionContext);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}
