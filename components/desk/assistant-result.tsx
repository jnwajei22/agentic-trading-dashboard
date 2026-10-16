export function AssistantResult({ result, title }: { result: Record<string, unknown>; title: string }) {
  const preview = (result.preview && typeof result.preview === "object" ? result.preview : result) as Record<string, unknown>
  const fields = [["Environment", "environment"], ["Account", "account_alias"], ["Instrument", "pair"], ["Side", "side"], ["Order type", "order_type"],
    ["Quantity (units)", "quantity"], ["Lots", "lot_size"], ["Entry", "entry"], ["Stop loss", "stop_loss"], ["Take profit", "take_profit"], ["Estimated risk", "estimated_risk"],
    ["Estimated margin", "estimated_margin"], ["Target", "target_id"], ["Expires", "expires_at"]]
  const structured = Boolean(result.preview || result.preview_id || result.execution_id || result.run_id)
  return <div><p className="font-medium">{title}</p><p className="mt-1 text-xs font-semibold uppercase text-emerald-600">{String(result.status ?? result.outcome ?? "Result").replaceAll("_", " ")}</p>
    {structured && <dl className="mt-3 grid grid-cols-2 gap-2">{fields.filter(([, key]) => preview[key] != null).map(([label, key]) => <div key={key}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="break-words text-sm">{String(preview[key])}</dd></div>)}</dl>}
    <details className="mt-2"><summary className="cursor-pointer text-xs text-muted-foreground">{structured ? "Validation and activity details" : "View result"}</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(result, null, 2)}</pre></details>
  </div>
}
