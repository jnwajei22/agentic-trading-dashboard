import { NextRequest, NextResponse } from "next/server"
import { auth0 } from "@/lib/afd/auth"
import { AfdBackendError } from "@/lib/afd/errors"
import { forwardWithAuthorization } from "@/lib/afd/backend"
import { afdBackendPath, isAllowedAfdRoute } from "@/lib/afd/routes"

type Context = { params: Promise<{ path: string[] }> }

async function forwardAfdRequest(request: NextRequest, segments: string[], method: string) {
  const path = segments.join("/")
  if (!isAllowedAfdRoute(path, method)) return NextResponse.json({ error: "Not found" }, { status: 404 })
  try {
    const session = await auth0.getSession()
    if (!session) return NextResponse.json({ error: "Please log in.", code: "not_authenticated" }, { status: 401 })
    const { token } = await auth0.getAccessToken()
    const body = method === "GET" || method === "DELETE" ? undefined : await request.text()
    const result = await forwardWithAuthorization<unknown>(afdBackendPath(segments, request.nextUrl.search), `Bearer ${token}`, { method, body: body || undefined })
    return NextResponse.json(result.data, { status: result.status })
  } catch (error) {
    if (error instanceof AfdBackendError) return NextResponse.json(error.payload && Object.keys(error.payload).length ? error.payload : { error: error.message, code: error.code }, { status: error.status })
    console.error("[afd proxy] unexpected forwarding failure", { path, method, error: error instanceof Error ? error.name : "UnknownError" })
    return NextResponse.json({ error: "Unable to reach the backend." }, { status: 502 })
  }
}

const handler = (method: string) => async (request: NextRequest, context: Context) => forwardAfdRequest(request, (await context.params).path, method)
export const GET = handler("GET")
export const POST = handler("POST")
export const PUT = handler("PUT")
export const PATCH = handler("PATCH")
export const DELETE = handler("DELETE")
