"use client";
import Link from "next/link";
import { useState } from "react";
import { ArrowUpRight, Check, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
export interface SetupStep {
  label: string;
  href: string;
  complete: boolean;
}
export function OnboardingChecklist({ steps }: { steps: SetupStep[] }) {
  const [expanded, setExpanded] = useState(false);
  const done = steps.filter((step) => step.complete).length;
  const next = steps.find((step) => !step.complete);
  if (!next) return null;
  return (
    <section
      className="rounded-3xl bg-card p-5 shadow-sm ring-1 ring-border/50 sm:p-6"
      aria-label="Desk setup"
    >
      <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-4 sm:grid-cols-[auto_minmax(0,1fr)_auto_auto]">
        <div className="relative grid h-11 w-11 shrink-0 place-items-center rounded-full bg-emerald-500/10 text-xs font-semibold text-emerald-700 dark:text-emerald-300">
          {done}/{steps.length}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">Make this desk yours</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Next up: {next.label.toLowerCase()}.
          </p>
        </div>
        <Button
          asChild
          size="sm"
          className="col-span-3 row-start-2 rounded-full sm:col-span-1 sm:col-start-3 sm:row-start-1"
        >
          <Link href={next.href}>
            Continue setup
            <ArrowUpRight className="h-4 w-4" />
          </Link>
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="col-start-3 row-start-1 h-9 w-9 rounded-full sm:col-start-4"
          aria-label={expanded ? "Hide setup steps" : "Show setup steps"}
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          <ChevronDown
            className={`h-4 w-4 transition-transform ${expanded ? "rotate-180" : ""}`}
          />
        </Button>
      </div>
      {expanded && (
        <ol className="mt-5 grid gap-x-6 gap-y-1 border-t border-border/50 pt-4 sm:grid-cols-2 lg:grid-cols-3">
          {steps.map((step, index) => (
            <li key={step.label}>
              <Link
                href={step.href}
                className="flex items-center gap-3 rounded-xl px-2 py-3 text-sm hover:bg-muted/60"
              >
                <span
                  className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs ${step.complete ? "bg-emerald-500/10 text-emerald-600" : "bg-muted text-muted-foreground"}`}
                >
                  {step.complete ? <Check className="h-3 w-3" /> : index + 1}
                </span>
                <span
                  className={
                    step.complete ? "text-muted-foreground" : "font-medium"
                  }
                >
                  {step.label}
                </span>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
