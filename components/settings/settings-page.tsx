import Link from "next/link"
import { ArrowLeft } from "lucide-react"
import { Button } from "@/components/ui/button"
import { PageHeader } from "@/components/desk/page-header"

export function SettingsPageHeader({ title, description, actions }: { title: string; description: string; actions?: React.ReactNode }) {
  return <div className="space-y-4">
    <Button asChild variant="ghost" size="sm" className="-ml-3 w-fit text-muted-foreground">
      <Link href="/settings"><ArrowLeft className="mr-2 h-4 w-4" />All settings</Link>
    </Button>
    <PageHeader eyebrow="Settings" title={title} description={description} actions={actions} />
  </div>
}

export function SettingsNotice({ message, tone = "neutral" }: { message: string; tone?: "neutral" | "good" | "bad" }) {
  const style = tone === "good" ? "border-emerald-500/30 bg-emerald-500/10" : tone === "bad" ? "border-red-500/30 bg-red-500/10" : "border-border bg-muted/30"
  return <p role="status" className={`rounded-lg border p-3 text-sm ${style}`}>{message}</p>
}
