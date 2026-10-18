"use client";
import Link from "next/link";
import { Suspense, useState } from "react";
import { usePathname } from "next/navigation";
import {
  Activity,
  Bot,
  CalendarClock,
  Gauge,
  LineChart,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "lucide-react";
import type { Account } from "@/lib/afd/contracts";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { AssistantPanel } from "./assistant-panel";
import { AccountProvider } from "./account-provider";
import { AccountSwitcher } from "./account-switcher";
import { CommandPalette } from "./command-palette";
const navigation = [
  ["Overview", "/dashboard", Gauge],
  ["Markets", "/markets", LineChart],
  ["Trade", "/trade", SlidersHorizontal],
  ["Autonomous", "/autonomous", Bot],
  ["Schedules", "/schedules", CalendarClock],
  ["Activity", "/activity", Activity],
  ["Settings", "/settings", Settings],
] as const;
function DeskNav({
  close,
  collapsed = false,
}: {
  close?: () => void;
  collapsed?: boolean;
}) {
  const pathname = usePathname();
  return (
    <nav aria-label="Primary navigation" className="space-y-1.5">
      {navigation.map(([label, href, Icon], index) => {
        const active =
          pathname === href ||
          (href !== "/dashboard" && pathname.startsWith(`${href}/`));
        return (
          <div key={href}>
            {!collapsed && (index === 0 || index === 3) && (
              <p
                className={cn(
                  "px-4 pb-3 text-[10px] font-medium uppercase tracking-[.16em] text-muted-foreground/70",
                  index === 3 && "pt-7",
                )}
              >
                {index === 0 ? "Workspace" : "Operations"}
              </p>
            )}
            <Link
              href={href}
              title={collapsed ? label : undefined}
              aria-label={collapsed ? label : undefined}
              onClick={close}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-2xl px-4 py-3 text-[13px] font-medium text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                collapsed && "justify-center px-2",
                active &&
                  "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
              )}
            >
              <Icon className="h-[18px] w-[18px] shrink-0" />
              {!collapsed && label}
              {active && !collapsed && (
                <span className="ml-auto h-1.5 w-1.5 rounded-full bg-emerald-500" />
              )}
            </Link>
          </div>
        );
      })}
    </nav>
  );
}
type ShellProps = {
  children: React.ReactNode;
  user: { name?: string | null; email?: string | null };
  accounts: Account[];
};
export function AppShell({ children, accounts, ...props }: ShellProps) {
  return (
    <AccountProvider accounts={accounts}>
      <ShellContent {...props}>{children}</ShellContent>
    </AccountProvider>
  );
}
function ShellContent({
  children,
  user,
}: Omit<ShellProps, "accounts">) {
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className="min-h-screen bg-background">
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 hidden bg-card/70 lg:flex lg:flex-col",
          collapsed ? "w-20" : "w-60",
        )}
      >
        <Brand collapsed={collapsed} />
        <div className="flex-1 overflow-y-auto px-4 py-5">
          <DeskNav collapsed={collapsed} />
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="mb-5 self-center rounded-full text-muted-foreground"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
        </Button>
      </aside>
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            aria-label="Close navigation"
            onClick={() => setOpen(false)}
          />
          <aside className="relative h-full w-72 bg-card">
            <div className="flex items-center justify-between pr-3">
              <Brand />
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setOpen(false)}
                aria-label="Close navigation"
              >
                <X />
              </Button>
            </div>
            <div className="p-4">
              <DeskNav close={() => setOpen(false)} />
            </div>
          </aside>
        </div>
      )}
      <div className={collapsed ? "lg:pl-20" : "lg:pl-60"}>
        <header className="sticky top-0 z-30 flex h-20 items-center gap-2 bg-background/85 px-4 backdrop-blur-xl sm:px-8 lg:px-10">
          <Button
            className="lg:hidden"
            variant="ghost"
            size="icon"
            onClick={() => setOpen(true)}
            aria-label="Open navigation"
          >
            <Menu />
          </Button>
          <div className="min-w-0 flex-1">
            <AccountSwitcher />
          </div>
          <div className="hidden md:block">
            <CommandPalette />
          </div>
          <ThemeToggle />
          <div className="ml-2 hidden items-center gap-3 border-l border-border/50 pl-5 sm:flex">
            <span
              className="grid h-8 w-8 place-items-center rounded-full bg-muted text-xs font-medium"
              aria-hidden="true"
            >
              SW
            </span>
            <span className="text-xs text-muted-foreground">
              {user.name ?? "Shared workspace"}
            </span>
          </div>
        </header>
        <main className="mx-auto max-w-[1440px] px-4 pb-24 pt-4 sm:px-8 lg:px-10 lg:pt-6">
          {children}
        </main>
        <Suspense fallback={null}>
          <AssistantPanel />
        </Suspense>
      </div>
    </div>
  );
}
function Brand({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <Link
      href="/dashboard"
      aria-label="Agentic Trading Desk dashboard"
      className={cn(
        "flex h-20 items-center gap-3 px-7",
        collapsed && "justify-center px-2",
      )}
    >
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-2xl bg-emerald-500 text-slate-950">
        <ShieldCheck className="h-5 w-5" />
      </span>
      {!collapsed && (
        <span>
          <strong className="block text-[17px] font-semibold tracking-tight">
            Agentic
            <span className="text-emerald-600 dark:text-emerald-400">.</span>
          </strong>
          <span className="block text-[9px] font-medium uppercase tracking-[.2em] text-muted-foreground">
            Trading desk
          </span>
        </span>
      )}
    </Link>
  );
}
