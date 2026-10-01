/** Session token storage.

Kept in localStorage for the prototype. The honest trade-off: localStorage is
readable by any script on the origin, so an XSS bug becomes token theft. A
production deployment should use an httpOnly, SameSite cookie — which also
needs CSRF protection and a backend session store. Listed under Production in
the README rather than quietly pretended away. */

const TOKEN_KEY = "barathseva.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
}
