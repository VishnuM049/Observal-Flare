"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { auth } from "@/lib/api-client";

function LoginContent() {
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const clientId = process.env.NEXT_PUBLIC_GITHUB_CLIENT_ID || "";

  useEffect(() => {
    const code = searchParams.get("code");
    if (code) {
      setLoading(true);
      auth
        .login(code)
        .then(() => {
          window.location.href = "/sites";
        })
        .catch((err) => {
          setError(err.message);
          setLoading(false);
        });
    }
  }, [searchParams]);

  const githubUrl = `https://github.com/login/oauth/authorize?client_id=${clientId}&scope=read:org`;

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6">
      <h1 className="text-3xl font-bold" style={{ color: "var(--color-ink)" }}>
        Sign in to Flare
      </h1>
      <p style={{ color: "var(--color-ink-muted)" }}>
        Manage Observal preview environments
      </p>
      {error && (
        <div className="card px-4 py-3 flex items-center gap-2 text-sm" style={{ borderColor: "var(--color-danger)", backgroundColor: "var(--color-danger-light)", color: "var(--color-danger)" }}>
          <span>!</span>
          <span>{error}</span>
        </div>
      )}
      {loading ? (
        <div className="flex items-center gap-2 text-sm" style={{ color: "var(--color-ink-muted)" }}>
          <span className="inline-block w-4 h-4 border-2 rounded-full animate-spin" style={{ borderColor: "var(--color-border)", borderTopColor: "var(--color-accent)" }} />
          Signing in...
        </div>
      ) : (
        <a href={githubUrl} className="btn-primary px-8 py-3">
          Sign in with GitHub &rarr;
        </a>
      )}
      <button
        onClick={() => {
          setLoading(true);
          auth
            .devLogin()
            .then(() => {
              window.location.href = "/sites";
            })
            .catch((err) => {
              setError(err.message);
              setLoading(false);
            });
        }}
        className="text-sm underline"
        style={{ color: "var(--color-ink-muted)" }}
      >
        Dev Login (local only)
      </button>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center py-20">
          <span className="inline-block w-5 h-5 border-2 rounded-full animate-spin" style={{ borderColor: "var(--color-border)", borderTopColor: "var(--color-accent)" }} />
        </div>
      }
    >
      <LoginContent />
    </Suspense>
  );
}
