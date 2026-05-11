import type { Metadata } from "next";
import { LogoutButton } from "@/components/logout-button";
import "./globals.css";

export const metadata: Metadata = {
  title: "Flare — Observal Provisioning",
  description: "Create, manage, and destroy Observal instances",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-gray-50 text-gray-900 antialiased min-h-screen">
        <nav className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
          <a href="/sites" className="text-xl font-bold tracking-tight">
            Flare
          </a>
          <div className="flex items-center gap-4 text-sm">
            <a href="/sites" className="hover:text-blue-600">
              Sites
            </a>
            <a href="/sites/new" className="hover:text-blue-600">
              New Site
            </a>
            <a href="/admin/costs" className="hover:text-blue-600">
              Costs
            </a>
            <a href="/admin/audit" className="hover:text-blue-600">
              Audit Log
            </a>
            <LogoutButton />
          </div>
        </nav>
        <main className="max-w-6xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
