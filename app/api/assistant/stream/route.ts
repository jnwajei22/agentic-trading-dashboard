import { NextRequest, NextResponse } from "next/server"
import { auth0 } from "@/lib/afd/auth"
import { resolveBackendBaseUrl } from "@/lib/afd/backend-runtime"

export const runtime = "nodejs"
export const maxDuration = 300

export async function POST(request: NextRequest) {
  // Same-origin cookie-authenticated mutation; no permissive cross-origin streaming.
  if (request.headers.get("origin") !== request.nextUrl.origin) return NextResponse.json({ error: "Origin not allowed." }, { status: 403 })
  if (!request.headers.get("content-type")?.includes("application/json")) return NextResponse.json({ error: "JSON required." }, { status: 415 })
  try {
    if (!await auth0.getSession()) return NextResponse.json({ error: "Please log in." }, { status: 401 })
    const { token } = await auth0.getAccessToken()
    const body = await request.text()
    if (body.length > 20000) return NextResponse.json({ error: "Message too large." }, { status: 413 })
    const upstream = await fetch(`${resolveBackendBaseUrl()}/api/assistant/stream`, {
      method: "POST", body, cache: "no-store",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(250000)]),
    })
    if (!upstream.ok) return NextResponse.json({ error: "The assistant could not start. Check account, profile and mode permissions." }, { status: upstream.status })
    if (!upstream.headers.get("content-type")?.includes("text/event-stream")) return NextResponse.json({ error: "Invalid assistant response." }, { status: 502 })
    return new Response(upstream.body, { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no" } })
  } catch {
    return NextResponse.json({ error: "Assistant unavailable." }, { status: 502 })
  }
}
