import { redirect } from "next/navigation"
import { auth0 } from "@/lib/afd/auth"

export default async function Home() {
  const session = await auth0.getSession()
  redirect(session ? "/dashboard" : "/login")
}
