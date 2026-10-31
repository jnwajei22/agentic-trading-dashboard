export type BrokerRow = {
  id: string; symbol: string; side: string | null; quantity: number | null;
  entry: number | null; pnl: number | null; stop_loss: number | null;
  take_profit: number | null; status: string | null; type: string | null;
  time?: string | number | null;
}

export function eventTime(value: BrokerRow["time"]): number | null {
  if (value == null || value === "") return null
  const numeric = Number(value)
  const result = Number.isFinite(numeric) ? (numeric < 1e12 ? numeric * 1000 : numeric) : Date.parse(String(value))
  return Number.isFinite(result) && result > 0 ? result : null
}

export function statusGroup(status: string | null) {
  const value = status?.toLowerCase().replace(/[\s_-]/g, "") ?? ""
  if (["filled", "executed"].includes(value)) return "filled"
  if (["cancelled", "canceled", "expired"].includes(value)) return "cancelled"
  if (value === "rejected") return "rejected"
  return "other"
}

export function activityLabel(value: string | null) {
  if (!value) return "Unavailable"
  return value.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[_-]/g, " ").replace(/^./, c => c.toUpperCase())
}

export function activityNumber(value: number | null, maximumFractionDigits = 5) {
  if (value == null || !Number.isFinite(Number(value))) return "—"
  return new Intl.NumberFormat(undefined, { maximumFractionDigits }).format(Number(value))
}

export function historyRows(rows: BrokerRow[], query: string, filter: string) {
  const term = query.trim().toLowerCase()
  return rows.map((row, index) => ({row, index}))
    .filter(({row}) => (!term || [row.symbol, row.id, row.side, row.type, row.status].some(value => value?.toLowerCase().includes(term))) && (filter === "all" || statusGroup(row.status) === filter))
    .sort((a, b) => (eventTime(b.row.time) ?? 0) - (eventTime(a.row.time) ?? 0) || a.index - b.index)
    .map(({row}) => row)
}
