"use client";

/** Client-side session state.

The server is the authority on roles — these helpers only decide what to
render. Every protected endpoint re-checks the caller's role, so hiding a link
here is a usability choice, never the security boundary.
*/

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import * as api from "./api";
import { clearToken, getToken, setToken } from "./token";
import type { AuthUser, Role } from "./types";

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<AuthUser>;
  signUp: (payload: {
    email: string;
    password: string;
    display_name: string;
    phone?: string;
  }) => Promise<AuthUser>;
  signOut: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

/** Where each role lands after signing in. */
export function homeForRole(role: Role): string {
  if (role === "SUPER_ADMIN") return "/admin";
  if (role === "DEPT_ADMIN") return "/department";
  return "/my-reports";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await api.fetchMe());
    } catch {
      // Expired or revoked: drop it rather than leaving a dead token around.
      clearToken();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signIn = useCallback(async (email: string, password: string) => {
    const session = await api.login({ email, password });
    setToken(session.access_token);
    setUser(session.user);
    return session.user;
  }, []);

  const signUp = useCallback(
    async (payload: {
      email: string;
      password: string;
      display_name: string;
      phone?: string;
    }) => {
      const session = await api.register(payload);
      setToken(session.access_token);
      setUser(session.user);
      return session.user;
    },
    [],
  );

  const signOut = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, signIn, signUp, signOut, refresh }),
    [user, loading, signIn, signUp, signOut, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}

/**
 * Gate a page on one or more roles.
 *
 * Returns `ready` once the session is resolved and permitted. Redirects to the
 * login page otherwise. The backend enforces the same rule independently.
 */
export function useRequireRole(roles: Role[]): {
  ready: boolean;
  user: AuthUser | null;
} {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace(
        `/login?next=${encodeURIComponent(window.location.pathname)}`,
      );
      return;
    }
    if (!roles.includes(user.role)) {
      router.replace(homeForRole(user.role));
    }
  }, [loading, user, roles, router]);

  return {
    ready: !loading && !!user && roles.includes(user.role),
    user,
  };
}
