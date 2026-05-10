"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { auth } from "@/lib/api-client";

function LoginContent() {
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  const clientId = process.env.NEXT_PUBLIC_GITHUB_CLIENT_ID || "";

  useEffect(() => {
    const code = searchParams.get("code");
    if (code) {
      auth
        .login(code)
        .then(() => {
          window.location.href = "/sites";
        })
        .catch((err) => setError(err.message));
    }
  }, [searchParams]);

  const githubUrl = `https://github.com/login/oauth/authorize?client_id=${clientId}&scope=read:org`;

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6">
      <h1 className="text-3xl font-bold">Sign in to Flare</h1>
      <p className="text-gray-500">
        Manage Observal preview environments
      </p>
      {error && (
        <div className="bg-red-50 text-red-700 px-4 py-2 rounded-md text-sm">
          {error}
        </div>
      )}
      <a
        href={githubUrl}
        className="bg-gray-900 text-white px-6 py-3 rounded-lg font-medium hover:bg-gray-800 transition-colors"
      >
        Sign in with GitHub
      </a>
      <button
        onClick={() => {
          auth
            .devLogin()
            .then(() => {
              window.location.href = "/sites";
            })
            .catch((err) => setError(err.message));
        }}
        className="text-sm text-gray-400 hover:text-gray-600 underline"
      >
        Dev Login (local only)
      </button>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="flex justify-center py-20">Loading...</div>}>
      <LoginContent />
    </Suspense>
  );
}
