"use client";

import { auth } from "@/lib/api-client";
import { useRouter } from "next/navigation";

export function LogoutButton() {
  const router = useRouter();

  async function handleLogout() {
    await auth.logout();
    router.push("/login");
  }

  return (
    <button
      onClick={handleLogout}
      className="text-sm transition-colors cursor-pointer"
      style={{ color: "var(--color-ink-muted)" }}
      onMouseEnter={(e) => (e.currentTarget.style.color = "var(--color-danger)")}
      onMouseLeave={(e) => (e.currentTarget.style.color = "var(--color-ink-muted)")}
    >
      Logout
    </button>
  );
}
