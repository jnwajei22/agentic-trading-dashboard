import { cn } from "@/lib/utils"

export function StatusPill({ label, tone = "neutral" }: { label: string; tone?: "good" | "warn" | "bad" | "neutral" }) {
  return <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium", tone === "good" && "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300", tone === "warn" && "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300", tone === "bad" && "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300", tone === "neutral" && "bg-muted text-muted-foreground")}><span className="h-1.5 w-1.5 rounded-full bg-current" />{label}</span>
}
