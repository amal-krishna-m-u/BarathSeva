"use client";

/** Navigation reflects the signed-in role.

Hiding a link is a usability choice, not a security boundary: every protected
endpoint re-checks the caller's role server-side, so a hand-typed URL gets the
same 401/403 a hidden link would have. */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

const CITIZEN_LINKS = [
  { href: "/", label: "Report" },
  { href: "/track", label: "Track" },
];

export default function Nav() {
  const { user, loading, signOut } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  const links = [...CITIZEN_LINKS];
  if (user) links.push({ href: "/my-reports", label: "My reports" });
  if (user?.role === "DEPT_ADMIN" || user?.role === "SUPER_ADMIN") {
    links.push({ href: "/department", label: "Department desk" });
  }
  if (user?.role === "SUPER_ADMIN") {
    links.push(
      { href: "/admin", label: "Command Center" },
      { href: "/admin/hotspots", label: "Hotspots" },
    );
  }

  function handleSignOut() {
    signOut();
    router.push("/");
  }

  return (
    <header className="sticky top-0 z-50 border-b border-ink-700/70 bg-ink-950/85 backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-5 gap-y-3 px-4 py-3 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-500/15 ring-1 ring-brand-500/40">
            <svg
              viewBox="0 0 24 24"
              className="h-4.5 w-4.5 text-brand-400"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M12 21s-7-5.1-7-10a7 7 0 1 1 14 0c0 4.9-7 10-7 10Z" />
              <circle cx="12" cy="11" r="2.5" />
            </svg>
          </span>
          <span className="leading-tight">
            <span className="block text-sm font-semibold tracking-tight">
              BarathSeva AI
            </span>
            <span className="block text-[11px] text-mute">
              Civic Complaint Intelligence
            </span>
          </span>
        </Link>

        <nav className="flex flex-wrap items-center gap-1 text-sm">
          {links.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-md px-3 py-1.5 transition ${
                  active
                    ? "bg-ink-800 text-white"
                    : "text-slate-300 hover:bg-ink-800 hover:text-white"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <span className="hidden rounded-full bg-amber-500/10 px-2.5 py-1 text-[11px] font-medium text-amber-300 ring-1 ring-amber-500/30 sm:inline">
            Prototype · mock gov APIs
          </span>

          {loading ? null : user ? (
            <div className="flex items-center gap-2">
              <span className="hidden text-right leading-tight sm:block">
                <span className="block text-xs font-medium text-slate-200">
                  {user.display_name}
                </span>
                <span className="block text-[10px] text-mute">
                  {user.department
                    ? `${user.department.code} · ${user.role.replace("_", " ").toLowerCase()}`
                    : user.role.replace("_", " ").toLowerCase()}
                </span>
              </span>
              <button
                onClick={handleSignOut}
                className="rounded-lg border border-ink-600 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-ink-800"
              >
                Sign out
              </button>
            </div>
          ) : (
            <Link
              href="/login"
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-ink-950 transition hover:bg-brand-500"
            >
              Sign in
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
