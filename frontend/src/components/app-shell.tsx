"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { Coins, Images, Inbox, LayoutDashboard, Megaphone, Menu, Settings, X } from "lucide-react";
import { keys, swrFetcher } from "@/lib/api";
import type { Workspace } from "@/lib/types";
import { cn, formatNumber } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/campaigns", label: "Campaigns", icon: Megaphone },
  { href: "/inbox", label: "Inbox", icon: Inbox },
  { href: "/proofs", label: "Proofs", icon: Images },
  { href: "/settings", label: "Settings", icon: Settings },
];

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2 text-lg font-semibold tracking-tight">
      <span className="inline-block h-6 w-6 rounded-md bg-primary" aria-hidden />
      raven
    </Link>
  );
}

function CreditsBox() {
  const { data, error } = useSWR<Workspace>(keys.workspace(), swrFetcher, { refreshInterval: 30_000 });
  return (
    <div className="rounded-lg border bg-background p-3">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        <Coins className="h-3.5 w-3.5" /> Credits
      </div>
      <div className="mt-1 text-xl font-semibold tabular">
        {error ? <span className="text-sm font-normal text-destructive">Unavailable</span> : data ? formatNumber(data.credits_balance) : "…"}
      </div>
      {data && <div className="truncate text-xs text-muted-foreground">{data.name}</div>}
    </div>
  );
}

function NavLinks({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-1">
      {NAV.map(({ href, label, icon: Icon }) => (
        <Link
          key={href}
          href={href}
          onClick={onNavigate}
          className={cn(
            "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
            isActive(pathname, href) ? "bg-secondary text-foreground" : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
          )}
        >
          <Icon className="h-4 w-4" />
          {label}
        </Link>
      ))}
    </nav>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  useEffect(() => setOpen(false), [pathname]);

  return (
    <div className="min-h-screen bg-muted/40">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-60 flex-col gap-6 border-r bg-background px-4 py-5 lg:flex">
        <Brand />
        <NavLinks pathname={pathname} />
        <div className="mt-auto">
          <CreditsBox />
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b bg-background px-4 lg:hidden">
        <Brand />
        <button
          type="button"
          aria-label={open ? "Close menu" : "Open menu"}
          onClick={() => setOpen((o) => !o)}
          className="rounded-md p-2 hover:bg-secondary"
        >
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </header>
      {open && (
        <div className="fixed inset-x-0 top-14 z-20 border-b bg-background px-4 pb-4 pt-2 shadow-sm lg:hidden">
          <NavLinks pathname={pathname} onNavigate={() => setOpen(false)} />
          <div className="mt-3">
            <CreditsBox />
          </div>
        </div>
      )}

      <main className="lg:pl-60">
        <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</div>
      </main>
    </div>
  );
}
