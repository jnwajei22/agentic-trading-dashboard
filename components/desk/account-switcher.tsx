"use client";
import Link from "next/link";
import { useState } from "react";
import {
  Check,
  ChevronDown,
  Plus,
  Search,
  Settings,
  Wallet,
} from "lucide-react";
import { useAccountContext } from "./account-provider";
import { formatEnum } from "@/lib/afd/display";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
export function AccountSwitcher() {
  const { accounts, selected, selecting, select } = useAccountContext();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const shown = accounts.filter((account) =>
    `${account.account_number} ${account.nickname ?? ""} ${account.broker_name} ${account.currency}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  async function choose(id: string) {
    setError("");
    try {
      await select(id);
      setOpen(false);
    } catch {
      setError("Could not switch accounts. Try again.");
    }
  }
  return (
    <>
      <Button
        variant="ghost"
        className="h-11 max-w-80 gap-3 rounded-full px-3 text-left"
        onClick={() => setOpen(true)}
        aria-label="Switch trading account"
      >
        <span className="hidden h-8 w-8 shrink-0 place-items-center rounded-full bg-emerald-500/10 text-emerald-600 sm:grid">
          <Wallet className="h-4 w-4" />
        </span>
        <span className="min-w-0">
          <strong className="block truncate text-sm font-medium">
            {selected
              ? selected.nickname || `${selected.broker} account`
              : "Select an account"}
          </strong>
          <span className="block truncate text-[11px] text-muted-foreground">
            {selected
              ? `${formatEnum(selected.environment)} · ${selected.currency} · #${selected.accountNumber}`
              : "Your trading workspace"}
          </span>
        </span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="gap-5 rounded-3xl p-6 sm:p-7">
          <DialogHeader>
            <span className="mb-2 grid h-10 w-10 place-items-center rounded-2xl bg-emerald-500/10 text-emerald-600">
              <Wallet className="h-5 w-5" />
            </span>
            <DialogTitle>Your accounts</DialogTitle>
            <DialogDescription>
              Choose the account you want to work with.
            </DialogDescription>
          </DialogHeader>
          <div className="relative">
            <Search className="absolute left-3.5 top-3 h-4 w-4 text-muted-foreground" />
            <Input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="rounded-2xl bg-muted/50 pl-10"
              placeholder="Find an account…"
              aria-label="Search trading accounts"
            />
          </div>
          <div className="max-h-72 space-y-1 overflow-y-auto">
            {shown.map((account) => (
              <button
                key={account.public_id}
                disabled={selecting || !account.available}
                onClick={() => void choose(account.public_id)}
                className={`flex w-full items-center gap-3 rounded-2xl p-4 text-left transition-colors hover:bg-muted/70 disabled:opacity-50 ${account.is_default_analysis ? "bg-emerald-500/[.07]" : ""}`}
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-muted text-sm font-semibold">
                  {(account.broker_name || "A").slice(0, 1)}
                </span>
                <span className="min-w-0 flex-1">
                  <strong className="block truncate text-sm font-medium">
                    {account.nickname ||
                      `${account.broker_name || "Trading"} account`}
                  </strong>
                  <span className="mt-1 block truncate text-xs text-muted-foreground">
                    {formatEnum(account.environment)} ·{" "}
                    {account.currency || "Currency unavailable"} · #
                    {account.account_number}
                    {!account.available ? " · Unavailable" : ""}
                  </span>
                </span>
                {account.is_default_analysis && (
                  <span className="flex items-center gap-1.5 text-xs text-emerald-700 dark:text-emerald-300">
                    <Check className="h-4 w-4" />
                    <span className="hidden sm:inline">Primary</span>
                  </span>
                )}
              </button>
            ))}
            {!shown.length && (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No matching accounts.
              </p>
            )}
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/50 pt-4">
            <Button asChild variant="ghost" size="sm">
              <Link href="/settings/accounts" onClick={() => setOpen(false)}>
                <Settings className="h-4 w-4" />
                Manage accounts
              </Link>
            </Button>
            <Button asChild size="sm" className="rounded-full">
              <Link
                href="/connect-tradelocker?new=1"
                onClick={() => setOpen(false)}
              >
                <Plus className="h-4 w-4" />
                Add connection
              </Link>
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
