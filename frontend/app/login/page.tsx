"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { homeForRole, useAuth } from "@/lib/auth";
import { Card, CardHeader, ErrorNote, Spinner } from "@/components/ui";

/** Demo logins, mirrored from backend/app/core/city.py. Prototype only. */
const DEMO_ACCOUNTS = [
  ["admin@example.com", "Super admin — city-wide command center"],
  ["water@example.com", "BWSSB — Water & Sewerage desk"],
  ["power@example.com", "BESCOM — Electricity desk"],
  ["roads@example.com", "BBMP — Roads & Public Works desk"],
  ["citizen@example.com", "Citizen — files and tracks reports"],
] as const;

function LoginInner() {
  const { user, loading, signIn, signUp } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next");

  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already signed in: go where this role belongs.
  useEffect(() => {
    if (!loading && user) router.replace(next || homeForRole(user.role));
  }, [loading, user, next, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const account =
        mode === "signin"
          ? await signIn(email.trim(), password)
          : await signUp({
              email: email.trim(),
              password,
              display_name: displayName.trim(),
              phone: phone.trim() || undefined,
            });
      router.replace(next || homeForRole(account.role));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Spinner label="Checking session" />;

  return (
    <div className="mx-auto grid max-w-4xl gap-6 md:grid-cols-5">
      <div className="md:col-span-3">
        <h1 className="text-2xl font-semibold tracking-tight text-white">
          {mode === "signin" ? "Sign in" : "Create a citizen account"}
        </h1>
        <p className="mt-2 text-sm text-mute">
          {mode === "signin"
            ? "Citizens track their reports here. Department staff and city admins reach their own dashboards with the same form."
            : "An account lets you track every report you file and builds a trust score from confirmed ones."}
        </p>

        <Card className="mt-4">
          <CardHeader
            title={mode === "signin" ? "Credentials" : "New account"}
          />
          <form onSubmit={submit} className="space-y-3 p-4">
            {mode === "signup" ? (
              <input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Your name"
                required
                minLength={2}
                className="w-full rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
              />
            ) : null}

            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="Email"
              autoComplete="email"
              required
              className="w-full rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
            />

            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={
                mode === "signup" ? "Password (min 8 characters)" : "Password"
              }
              autoComplete={
                mode === "signup" ? "new-password" : "current-password"
              }
              required
              minLength={mode === "signup" ? 8 : 1}
              className="w-full rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
            />

            {mode === "signup" ? (
              <input
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                placeholder="Phone (optional) — links reports you filed before signing up"
                className="w-full rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
              />
            ) : null}

            {error ? <ErrorNote>{error}</ErrorNote> : null}

            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-brand-500 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-brand-400 disabled:opacity-50"
            >
              {busy
                ? "Working…"
                : mode === "signin"
                  ? "Sign in"
                  : "Create account"}
            </button>

            <p className="text-center text-xs text-mute">
              {mode === "signin" ? (
                <>
                  No account?{" "}
                  <button
                    type="button"
                    onClick={() => {
                      setMode("signup");
                      setError(null);
                    }}
                    className="text-brand-400 hover:underline"
                  >
                    Register as a citizen
                  </button>
                </>
              ) : (
                <>
                  Already registered?{" "}
                  <button
                    type="button"
                    onClick={() => {
                      setMode("signin");
                      setError(null);
                    }}
                    className="text-brand-400 hover:underline"
                  >
                    Sign in
                  </button>
                </>
              )}
            </p>
            <p className="text-center text-xs text-mute">
              You can also{" "}
              <Link href="/" className="text-brand-400 hover:underline">
                report anonymously
              </Link>{" "}
              without an account.
            </p>
          </form>
        </Card>
      </div>

      <aside className="md:col-span-2">
        <Card>
          <CardHeader
            title="Demo accounts"
            subtitle="Prototype only — seeded for exploration"
          />
          <ul className="divide-y divide-ink-700/70">
            {DEMO_ACCOUNTS.map(([account, description]) => (
              <li key={account}>
                <button
                  type="button"
                  onClick={() => {
                    setMode("signin");
                    setEmail(account);
                    setError(null);
                  }}
                  className="w-full px-4 py-2.5 text-left transition hover:bg-ink-800"
                >
                  <span className="block font-mono text-xs text-brand-400">
                    {account}
                  </span>
                  <span className="mt-0.5 block text-[11px] leading-relaxed text-mute">
                    {description}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <p className="border-t border-ink-700/70 px-4 py-3 text-[11px] leading-relaxed text-mute">
            Click an account to fill the email. The shared demo password is in{" "}
            <code className="font-mono text-slate-300">
              backend/app/core/city.py
            </code>{" "}
            and the README setup section. These accounts are seeded only outside
            production.
          </p>
        </Card>
      </aside>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<Spinner label="Loading" />}>
      <LoginInner />
    </Suspense>
  );
}
