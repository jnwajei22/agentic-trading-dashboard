import { NextRequest, NextResponse } from "next/server"
import { resolveBackendBaseUrl } from "@/lib/afd/backend-runtime"

export const maxDuration = 120

/** Stream bytes directly: the JSON proxy buffers responses and has a 30s deadline. */
export async function GET(request: NextRequest) {
  try {
    const upstream = await fetch(`${resolveBackendBaseUrl()}/api/markets/chart/stream${request.nextUrl.search}`, {
      cache: "no-store",
      headers: { Accept: "text/event-stream" },
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(110_000)]),
    })
    if (!upstream.ok) return NextResponse.json({ error: "The broker chart feed is unavailable." }, { status: upstream.status })
    if (!upstream.headers.get("content-type")?.includes("text/event-stream")) return NextResponse.json({ error: "Invalid chart feed response." }, { status: 502 })
    return new Response(upstream.body, { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no" } })
  } catch (error) {
    console.error("[chart stream] connection failed", { type: error instanceof Error ? error.name : "UnknownError" })
    return NextResponse.json({ error: "The chart feed could not connect." }, { status: 502 })
  }
}
