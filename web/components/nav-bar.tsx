"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { User } from "@/lib/types";
import { auth } from "@/lib/api-client";
import { LogoutButton } from "./logout-button";

const NAV_LINKS = [
  { href: "/sites", label: "Sites" },
  { href: "/sites/new", label: "New Site" },
  { href: "/admin/costs", label: "Costs" },
  { href: "/admin/audit", label: "Audit Log" },
];

export function NavBar() {
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    auth.me().then(setUser).catch(() => {});
  }, []);

  return (
    <nav className="bg-white border-b px-6 py-3 flex items-center justify-between" style={{ borderColor: "var(--color-border)" }}>
      <Link href="/sites" className="text-xl font-bold tracking-tight" style={{ color: "var(--color-ink)" }}>
        Flare
      </Link>
      <div className="flex items-center gap-5 text-sm">
        {NAV_LINKS.map((link) => {
          const active = pathname === link.href || (link.href !== "/sites" && pathname.startsWith(link.href));
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`transition-colors ${active ? "font-medium" : "hover:opacity-70"}`}
              style={{ color: active ? "var(--color-accent)" : "var(--color-ink-light)" }}
            >
              {link.label}
            </Link>
          );
        })}
        <span className="w-px h-4 bg-gray-200" />
        {user && (
          <span className="text-xs" style={{ color: "var(--color-ink-muted)" }}>
            {user.name}
          </span>
        )}
        <LogoutButton />
      </div>
    </nav>
  );
}
