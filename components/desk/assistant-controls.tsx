"use client";
import Link from "next/link";
import { Check, ChevronRight, MessageSquare } from "lucide-react";
import {
  assistantModes,
  type AssistantHistory,
} from "@/lib/afd/assistant-display";

type Profile = { public_id: string; name: string; account_id: string };
export function AssistantOptions({
  mode,
  profile,
  profiles,
  hasCurrentProfile,
  disabled,
  onMode,
  onProfile,
}: {
  mode: string;
  profile: string;
  profiles: Profile[];
  hasCurrentProfile: boolean;
  disabled: boolean;
  onMode: (mode: string) => void;
  onProfile: (profile: string) => void;
}) {
  return (
    <section
      aria-label="Assistant options"
      className="mx-6 border-b border-border/50 py-4"
    >
      <p className="mb-3 text-xs font-medium">How would you like to work?</p>
      <div role="radiogroup" aria-label="Assistant mode" className="space-y-1">
        {assistantModes.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={mode === option.value}
            disabled={disabled}
            onClick={() => onMode(option.value)}
            className={`flex w-full items-center gap-3 rounded-xl p-3 text-left transition-colors disabled:opacity-50 ${mode === option.value ? "bg-emerald-500/10" : "hover:bg-muted/60"}`}
          >
            <span className="min-w-0 flex-1">
              <span className="block text-xs font-semibold">
                {option.label}
              </span>
              <span className="mt-1 block text-[11px] leading-relaxed text-muted-foreground">
                {option.description}
              </span>
            </span>
            {mode === option.value && (
              <Check className="h-4 w-4 shrink-0 text-emerald-600" />
            )}
          </button>
        ))}
      </div>
      {mode !== "research" && (
        <div className="mt-4 border-t border-border/50 pt-4">
          <label className="block text-xs font-medium">
            Strategy
            <select
              aria-label="Assistant strategy"
              value={profile}
              disabled={disabled}
              onChange={(event) => onProfile(event.target.value)}
              className="mt-2 w-full rounded-xl border border-border/60 bg-background p-2.5 text-xs"
            >
              <option value="">
                {hasCurrentProfile
                  ? "Use current strategy"
                  : "Choose a strategy"}
              </option>
              {profiles.map((item) => (
                <option key={item.public_id} value={item.public_id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          {!profiles.length && !hasCurrentProfile && (
            <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
              <Link
                href="/autonomous?create=1"
                className="underline underline-offset-2"
              >
                Create a strategy
              </Link>{" "}
              to define your account and risk limits first.
            </p>
          )}
          <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
            {mode === "co_op"
              ? "You approve each action. Approvals expire after 60 seconds."
              : "Selecting this mode does not enable trading. Existing demo permissions and safety controls still apply."}
          </p>
        </div>
      )}
    </section>
  );
}

export function AssistantHistoryList({
  threads,
  current,
  loading,
  error,
  disabled,
  onSelect,
}: {
  threads: AssistantHistory[];
  current: string;
  loading: boolean;
  error: string;
  disabled: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <section aria-label="Chat history" className="space-y-3">
      <div>
        <h3 className="text-base font-semibold">Recent chats</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Pick up where you left off.
        </p>
      </div>
      {loading ? (
        <p role="status" className="py-6 text-sm text-muted-foreground">
          Loading your chats…
        </p>
      ) : error ? (
        <p role="alert" className="py-6 text-sm text-muted-foreground">
          {error}
        </p>
      ) : !threads.length ? (
        <div className="py-10 text-center">
          <MessageSquare className="mx-auto mb-3 h-6 w-6 text-muted-foreground" />
          <p className="text-sm font-medium">Your conversations start here</p>
          <p className="mt-2 text-xs text-muted-foreground">
            New chats will appear in this list.
          </p>
        </div>
      ) : (
        <div className="space-y-1">
          {threads.map((item) => (
            <button
              key={item.id}
              type="button"
              disabled={disabled}
              aria-current={item.id === current ? "true" : undefined}
              onClick={() => onSelect(item.id)}
              className={`flex w-full items-center gap-3 rounded-2xl px-3 py-4 text-left transition-colors hover:bg-muted/60 disabled:opacity-50 ${item.id === current ? "bg-muted/60" : ""}`}
            >
              <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">
                  {item.title?.trim() || "New chat"}
                </span>
                <span className="mt-1 block text-[11px] text-muted-foreground">
                  {assistantModes.find((option) => option.value === item.mode)
                    ?.label ?? "Chat"}
                  {item.created_at
                    ? ` · ${new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(item.created_at * 1000))}`
                    : ""}
                </span>
              </span>
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
