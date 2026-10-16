/** Next may normalize its internal URL hostname; compare the browser-facing Host. */
export function isSameOriginRequest(request: { headers: Headers; nextUrl: { protocol: string } }) {
  const origin = request.headers.get("origin")
  const host = request.headers.get("host")
  if (!origin || !host) return false
  try {
    const parsed = new URL(origin)
    return parsed.origin === origin && parsed.host === host && parsed.protocol === request.nextUrl.protocol
  } catch { return false }
}
