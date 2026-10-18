"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowUpRight, Wallet } from "lucide-react";
import { Button } from "@/components/ui/button";
import { mutateAfd, BrowserAfdError } from "@/lib/afd/browser-client";
import {
  normalizeDiscoveredAccounts,
  type DiscoveredAccount,
} from "@/lib/afd/onboarding";
import { formatEnum } from "@/lib/afd/display";
import { StatusPill } from "@/components/desk/status-pill";
export function AccountSelector({
  accounts,
  connectionId,
  initialError,
}: {
  accounts: Array<Record<string, unknown>>;
  connectionId?: string;
  initialError: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState(initialError);
  const discovered = normalizeDiscoveredAccounts(accounts);
  async function select(account: DiscoveredAccount) {
    setBusy(account.accountId);
    setError("");
    try {
      await mutateAfd("broker/tradelocker/select-account", "POST", {
        account_id: account.accountId,
        account_number: account.accountNumber,
        connection_id: connectionId,
      });
      router.replace("/dashboard?connected=1");
    } catch (e) {
      setError(
        e instanceof BrowserAfdError
          ? e.message
          : "The account could not be selected.",
      );
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="desk-surface w-full max-w-2xl p-6 sm:p-10">
      <span className="mb-7 grid h-12 w-12 place-items-center rounded-2xl bg-emerald-500/10 text-emerald-600">
        <Wallet className="h-6 w-6" />
      </span>
      <p className="desk-eyebrow">Trading workspace</p>
      <h1 className="mt-3 text-3xl font-medium tracking-tight sm:text-4xl">
        Choose your account.
      </h1>
      <p className="mt-4 max-w-md text-sm leading-relaxed text-muted-foreground">
        Select the account to use at your desk. You can switch between your
        accounts at any time.
      </p>
      {error && (
        <p
          role="alert"
          className="mt-6 rounded-2xl bg-red-500/10 p-4 text-sm text-red-700 dark:text-red-300"
        >
          {error}
        </p>
      )}
      <div className="mt-8 space-y-3">
        {discovered.map((account) => (
          <article
            key={`${account.accountId}-${account.accountNumber}`}
            className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-4 rounded-2xl sm:flex bg-muted/50 p-5"
          >
            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-background text-sm font-semibold">
              {account.brokerName.slice(0, 1)}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold">{account.brokerName}</h2>
                <StatusPill
                  label={
                    account.available
                      ? formatEnum(account.environment)
                      : "Unavailable"
                  }
                  tone={account.available ? "neutral" : "warn"}
                />
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground">
                Account #{account.accountNumber} · {account.currency}
              </p>
            </div>
            <Button
              className="col-span-2 rounded-full sm:col-span-1"
              size="sm"
              disabled={Boolean(busy) || !account.available}
              onClick={() => void select(account)}
            >
              {busy === account.accountId ? "Selecting…" : "Use account"}
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Button>
          </article>
        ))}
        {!discovered.length && !error && (
          <p className="rounded-2xl bg-muted/40 p-8 text-center text-sm text-muted-foreground">
            No accounts were returned by TradeLocker.
          </p>
        )}
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="mt-7 px-0 text-xs text-muted-foreground hover:bg-transparent"
        onClick={() => router.push("/connect-tradelocker")}
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to connection
      </Button>
    </section>
  );
}
