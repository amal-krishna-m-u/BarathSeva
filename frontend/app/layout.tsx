import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "BarathSeva AI — Civic Complaint Intelligence",
  description:
    "Autonomous civic complaint orchestration for Bengaluru: evidence " +
    "authentication, verification, ward mapping, department routing, SLA " +
    "monitoring and resolution from a single citizen message.",
};

const NAV = [
  { href: "/", label: "Report" },
  { href: "/track", label: "Track" },
  { href: "/admin", label: "Command Center" },
  { href: "/admin/hotspots", label: "Hotspots" },
];

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        <header className="sticky top-0 z-50 border-b border-ink-700/70 bg-ink-950/85 backdrop-blur-xl">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6">
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

            <nav className="flex items-center gap-1 text-sm">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="rounded-md px-3 py-1.5 text-slate-300 transition hover:bg-ink-800 hover:text-white"
                >
                  {item.label}
                </Link>
              ))}
            </nav>

            <div className="ml-auto flex items-center gap-2">
              <span className="rounded-full bg-ink-800 px-2.5 py-1 text-[11px] font-medium text-mute ring-1 ring-ink-600">
                Bengaluru
              </span>
              <span className="rounded-full bg-amber-500/10 px-2.5 py-1 text-[11px] font-medium text-amber-300 ring-1 ring-amber-500/30">
                Prototype · mock gov APIs
              </span>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">{children}</main>

        <footer className="mt-16 border-t border-ink-700/70 py-8">
          <div className="mx-auto max-w-7xl px-4 text-xs leading-relaxed text-mute sm:px-6">
            <p className="max-w-3xl">
              <strong className="font-semibold text-slate-300">
                Prototype scope.
              </strong>{" "}
              Government connectivity is served by mock BBMP / BWSSB / BESCOM
              endpoints — there are no real municipal integrations. Ward
              boundaries are approximate stand-ins for official BBMP geometry.
              Any ticket numbers shown are generated locally.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
