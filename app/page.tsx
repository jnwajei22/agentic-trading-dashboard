import { redirect } from "next/navigation"
import { auth0 } from "@/lib/afd/auth"

export default async function Home() {
  const session = await auth0.getSession().catch(() => null)
  redirect(session ? "/dashboard" : "/login")
}
