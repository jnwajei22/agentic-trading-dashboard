import { redirect } from "next/navigation"
import { auth0 } from "@/lib/afd/auth"

export async function requireSession(returnTo = "/dashboard") {
  const session = await auth0.getSession()
  if (!session) {
    redirect(`/auth/login?returnTo=${encodeURIComponent(returnTo)}`)
  }
  return session
}
