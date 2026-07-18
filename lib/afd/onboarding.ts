export type DiscoveredAccount = {
  accountId: string
  accountNumber: string
  name: string
  currency: string
  environment: string
  brokerName: string
  available: boolean
}

function text(row: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) if (row[key] != null && String(row[key]).trim()) return String(row[key]).trim()
  return ""
}

export function normalizeDiscoveredAccounts(rows: Array<Record<string, unknown>>): DiscoveredAccount[] {
  return rows.map((row) => {
    const accountId = text(row, "accountId", "account_id")
    const accountNumber = text(row, "accNum", "accountNumber", "account_number")
    const explicitEnvironment = text(row, "environment", "accountType", "type").toLowerCase()
    const isDemo = row.isDemo ?? row.is_demo
    const environment = explicitEnvironment || (isDemo === true || isDemo === 1 ? "demo" : isDemo === false || isDemo === 0 ? "live" : "unknown")
    const status = text(row, "status").toLowerCase()
    const available = typeof row.available === "boolean"
      ? row.available
      : !["inactive", "disabled", "closed", "blocked", "unavailable"].includes(status)
    return {
      accountId,
      accountNumber,
      name: text(row, "name", "accountName", "account_name") || `Account ${accountNumber}`,
      currency: text(row, "currency") || "Unavailable",
      environment,
      brokerName: text(row, "broker_name", "server") || "HeroFX",
      available,
    }
  }).filter((row) => row.accountId && row.accountNumber)
}
