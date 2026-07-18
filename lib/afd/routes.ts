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
  "user-preferences": ["GET", "PATCH"], "watchlists": ["GET", "POST"],
  "activity": ["GET"], "status": ["GET"],
  "providers": ["GET"], "trading/connections": ["GET"], "trading/accounts": ["GET"],
}
const dynamic: Array<[RegExp, readonly string[]]> = [
  [/^broker\/accounts\/[^/]+\/(alias|default|disable|enable)$/, ["PUT"]],
  [/^broker\/connections\/[^/]+\/disable$/, ["PUT"]],
  [/^broker\/connections\/[^/]+$/, ["DELETE"]],
  [/^providers\/[^/]+\/capabilities$/, ["GET"]],
  [/^trading\/connections\/[^/]+$/, ["POST", "DELETE"]],
  [/^trading\/accounts\/[^/]+\/(primary|nickname|enable)$/, ["PUT"]],
  [/^trading\/accounts\/[^/]+\/capabilities$/, ["GET"]],
  [/^execution-profiles\/[^/]+$/, ["GET", "PUT", "PATCH", "DELETE"]],
  [/^execution-profiles\/[^/]+\/capabilities$/, ["GET"]],
  [/^execution-profiles\/[^/]+\/autonomy\/status$/, ["GET"]],
  [/^execution-profiles\/[^/]+\/autonomy\/schedule$/, ["POST"]],
  [/^execution-profiles\/[^/]+\/demo-test$/, ["POST"]],
  [/^watchlists\/[^/]+$/, ["PUT", "DELETE"]],
  [/^accounts\/[^/]+\/(instruments|market-groups|market-universe)$/, ["GET"]],
  [/^accounts\/[^/]+\/tradability\/[^/]+$/, ["GET"]],
  [/^markets\/.+\/summary$/, ["GET"]],
  [/^markets\/.+$/, ["GET"]],
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
