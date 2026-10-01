import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Nav from "@/components/Nav";
import { AuthProvider } from "@/lib/auth";
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

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        <AuthProvider>
        <Nav />

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
        </AuthProvider>
      </body>
    </html>
  );
}
