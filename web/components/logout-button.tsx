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
      className="hover:text-red-600 text-sm cursor-pointer"
    >
      Logout
    </button>
  );
}
