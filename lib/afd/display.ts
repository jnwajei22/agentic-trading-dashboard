import type { Account } from "@/lib/afd/contracts"

const ACRONYMS: Record<string, string> = { ai: "AI", api: "API", usd: "USD", utc: "UTC", id: "ID",
  pnl: "P&L", mcp: "MCP", rr: "R:R" }

export function formatEnum(value: string | null | undefined) {
  if (!value) return "Unknown"
  return value.split(/[_\-\s]+/).filter(Boolean).map((part) => ACRONYMS[part.toLowerCase()] ??
    `${part.charAt(0).toUpperCase()}${part.slice(1).toLowerCase()}`).join(" ")
}

export const accountTitle = (account: Account) => account.account_number || account.account_name || "Account"
export const accountContext = (account: Account) => `${account.broker_name || "Broker"} · ${formatEnum(account.environment)}`
export const accountLabel = (account: Account) => `${accountTitle(account)} · ${accountContext(account)}`
