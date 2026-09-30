"use client";

import { ReactNode } from "react";

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm muted">
      <span
        aria-hidden
        className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
      />
      {label}
    </span>
  );
}

export function ErrorBox({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <div className="error whitespace-pre-wrap" role="alert">
      {children}
    </div>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  if (!children) return null;
  return <div className="notice">{children}</div>;
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="card px-6 py-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      {hint ? <p className="mt-1.5 text-sm muted">{hint}</p> : null}
    </div>
  );
}

export function Tag({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="tag" title={title}>
      {children}
    </span>
  );
}

/** A ready/not-ready dot. Colour alone never carries the meaning - the label
 *  next to it says the same thing in words. */
export function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      aria-hidden
      className="h-2 w-2 rounded-full"
      style={{ background: ok ? "var(--ok)" : "var(--ink-faint)" }}
    />
  );
}

export function SectionHead({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-4">
      <h2 className="text-[15px] font-semibold tracking-[-0.01em]">{title}</h2>
      {children ? <p className="mt-1 text-sm muted max-w-2xl">{children}</p> : null}
    </div>
  );
}
