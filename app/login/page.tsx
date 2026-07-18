import { redirect } from "next/navigation"
import { ShieldCheck } from "lucide-react"
import { auth0 } from "@/lib/afd/auth"
import { Button } from "@/components/ui/button"

export const metadata = { title: "Log in" }
export default async function LoginPage() {
  if (await auth0.getSession().catch(() => null)) redirect("/dashboard")
  return <main className="grid min-h-screen place-items-center bg-muted/30 p-4"><section className="w-full max-w-md rounded-2xl border bg-card p-8 shadow-sm"><span className="mb-6 grid h-12 w-12 place-items-center rounded-xl bg-emerald-600 text-white"><ShieldCheck /></span><p className="text-xs font-semibold uppercase tracking-[.18em] text-emerald-600">Secure workspace</p><h1 className="mt-2 text-3xl font-semibold">Agentic Trading Desk</h1><p className="mt-3 text-sm leading-6 text-muted-foreground">Forex research, execution, and autonomous trading workspace. Sign in with Auth0 to access your existing broker connections and profiles.</p><Button asChild className="mt-8 w-full"><a href="/auth/login?returnTo=/dashboard">Continue with Auth0</a></Button><p className="mt-5 text-center text-xs text-muted-foreground">Credentials and access tokens remain server-side.</p></section></main>
}
