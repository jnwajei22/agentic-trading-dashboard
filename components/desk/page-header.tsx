export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description: string; actions?: React.ReactNode }) {
  return <header className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between"><div>{eyebrow && <p className="mb-2 text-xs font-semibold uppercase tracking-[.18em] text-emerald-600 dark:text-emerald-400">{eyebrow}</p>}<h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1><p className="mt-2 max-w-3xl text-sm text-muted-foreground">{description}</p></div>{actions}</header>
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: React.ReactNode }) {
  return <div className="rounded-xl border border-dashed p-8 text-center"><h2 className="font-semibold">{title}</h2><p className="mx-auto mt-2 max-w-xl text-sm text-muted-foreground">{description}</p>{action && <div className="mt-5">{action}</div>}</div>
}
