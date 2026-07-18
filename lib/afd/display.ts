import type { Account } from "@/lib/afd/contracts"

const ACRONYMS: Record<string, string> = { ai: "AI", api: "API", usd: "USD", eur:"EUR",gbp:"GBP",jpy:"JPY",chf:"CHF",cad:"CAD",aud:"AUD",nzd:"NZD", utc: "UTC", id: "ID",ids:"IDs",
  pnl: "P&L", mcp: "MCP", rr: "R:R" }

export function formatEnum(value: string | null | undefined) {
  if (!value) return "Unknown"
  const parts=value.split(/[_\-\s/]+/).filter(Boolean)
  if(parts.length===2&&parts.every(part=>["usd","eur","gbp","jpy","chf","cad","aud","nzd"].includes(part.toLowerCase())))return parts.map(part=>ACRONYMS[part.toLowerCase()]).join("/")
  return parts.map((part,index) => ACRONYMS[part.toLowerCase()] ??
    (index>0&&["to","and","or","of"].includes(part.toLowerCase())?part.toLowerCase():`${part.charAt(0).toUpperCase()}${part.slice(1).toLowerCase()}`)).join(" ")
}

export const accountTitle = (account: Account) => account.account_number || account.account_name || "Account"
export const accountContext = (account: Account) => `${account.broker_name || "Broker"} · ${formatEnum(account.environment)}`
export const accountLabel = (account: Account) => `${accountTitle(account)} · ${accountContext(account)}`
