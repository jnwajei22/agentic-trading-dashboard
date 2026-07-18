const exact: Record<string, readonly string[]> = {
  me: ["GET"], "broker/status": ["GET"], "broker/onboarding-status": ["POST"],
  "broker/tradelocker/save-credentials": ["POST"], "broker/tradelocker/discover-accounts": ["POST"],
  "broker/tradelocker/select-account": ["POST"], "broker/tradelocker": ["DELETE"],
  "broker/connections": ["GET"], "broker/accounts": ["GET"], "execution-profiles": ["GET", "POST"],
  "autonomous-controls": ["GET", "PATCH"], "autonomous-controls/audit": ["GET"],
  "autonomous-runs": ["GET"], "autonomous-schedules": ["GET"], "autonomous-daily-summary": ["GET"],
  "autonomous-worker-health": ["GET"], "demo-executions": ["GET"], "operations/kill-switch/enable": ["POST"],
  "markets/overview": ["GET"], "markets/search": ["GET"], "markets/news": ["GET"],
  "markets/calendar": ["GET"], "markets/macro": ["GET"],
}
const dynamic: Array<[RegExp, readonly string[]]> = [
  [/^broker\/accounts\/[^/]+\/(alias|default|disable)$/, ["PUT"]],
  [/^broker\/connections\/[^/]+\/disable$/, ["PUT"]],
  [/^execution-profiles\/[^/]+$/, ["GET", "PUT", "PATCH", "DELETE"]],
  [/^execution-profiles\/[^/]+\/capabilities$/, ["GET"]],
  [/^execution-profiles\/[^/]+\/autonomy\/status$/, ["GET"]],
  [/^execution-profiles\/[^/]+\/autonomy\/schedule$/, ["POST"]],
  [/^accounts\/[^/]+\/(instruments|market-groups|market-universe)$/, ["GET"]],
  [/^accounts\/[^/]+\/tradability\/[^/]+$/, ["GET"]],
  [/^markets\/[^/]+$/, ["GET"]],
  [/^autonomous-runs\/[^/]+\/audit$/, ["GET"]],
  [/^autonomous-schedules\/[^/]+$/, ["GET", "PUT", "DELETE"]],
  [/^autonomous-schedules\/[^/]+\/(pause|resume)$/, ["POST"]],
  [/^autonomous-schedule-runs\/[^/]+\/retry$/, ["POST"]],
]

export function isAllowedAfdRoute(path: string, method: string) {
  const upper = method.toUpperCase()
  return exact[path]?.includes(upper) || dynamic.some(([pattern, methods]) => pattern.test(path) && methods.includes(upper))
}

export function afdBackendPath(segments: string[], search = "") {
  return `/api/${segments.map((segment) => encodeURIComponent(decodeURIComponent(segment))).join("/")}${search}`
}
