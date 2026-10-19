"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { AssistantResult } from "./assistant-result";
import {
  Bot,
  X,
  Square,
  Send,
  SlidersHorizontal,
  History as HistoryIcon,
  Plus,
  Sparkles,
  ArrowUpRight,
} from "lucide-react";
import {
  currentAssistantContext,
  type PageContext,
} from "@/lib/afd/assistant-context";
import { Button } from "@/components/ui/button";
import { useAccountContext } from "./account-provider";
import { browserAfdFetch, mutateAfd } from "@/lib/afd/browser-client";
import {
  consumeAssistantStream,
  type AssistantEvent,
} from "@/lib/afd/assistant";

type Profile = { public_id: string; name: string; account_id: string };
type Message = {
  kind: string;
  text?: string;
  tool?: string;
  result?: Record<string, unknown>;
};
import { AssistantOptions, AssistantHistoryList } from "./assistant-controls";
import {
  assistantModes,
  assistantPageLabel,
  assistantToolLabel,
  type AssistantHistory as History,
} from "@/lib/afd/assistant-display";

export function AssistantPanel() {
  const [pageContext, setPageContext] = useState<PageContext>({});
  const [open, setOpen] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [mode, setMode] = useState("research");
  const [profile, setProfile] = useState("");
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [threads, setThreads] = useState<History[]>([]);
  const [thread, setThread] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [used, setUsed] = useState<string[]>([]);
  const abort = useRef<AbortController | null>(null);
  const scroll = useRef<HTMLDivElement | null>(null);
  const pathname = usePathname();
  const query = useSearchParams();
  const { selected } = useAccountContext();
  const pageProfile = pathname.startsWith("/autonomous/")
    ? pathname.split("/").at(-1)
    : null;
  const boundProfile =
    profile || pageContext.profile_ref || pageProfile || null;
  const context = {
    ...pageContext,
    page: pathname,
    market:
      pageContext.market || query.get("instrument") || query.get("symbol"),
    account_ref: selected?.publicRef ?? null,
    profile_ref: boundProfile,
    schedule_id: pageContext.schedule_id || query.get("schedule_id"),
    run_id: pageContext.run_id || query.get("run_id"),
    preview_id: pageContext.preview_id || query.get("preview_id"),
  };

  useEffect(() => {
    setPageContext(currentAssistantContext());
    const update = (event: Event) =>
      setPageContext((event as CustomEvent<PageContext>).detail);
    window.addEventListener("desk-assistant-context", update);
    return () => window.removeEventListener("desk-assistant-context", update);
  }, []);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setHistoryLoading(true);
    setHistoryError("");
    void Promise.all([
      browserAfdFetch<{ profiles: Profile[] }>("execution-profiles"),
      browserAfdFetch<{ conversations: History[] }>("assistant/conversations"),
    ])
      .then(([p, h]) => {
        if (active) {
          setProfiles(p.profiles);
          setThreads(h.conversations);
        }
      })
      .catch(() => {
        if (active)
          setHistoryError(
            "Unable to load chats. Close and reopen the assistant to retry.",
          );
      })
      .finally(() => {
        if (active) setHistoryLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open, showHistory]);
  useEffect(() => {
    scroll.current?.scrollIntoView({ block: "end" });
  }, [messages]);
  useEffect(() => () => abort.current?.abort(), []);

  function reset() {
    setShowHistory(false);
    setInput("");
    setThread("");
    setMessages([]);
    setUsed([]);
    setError("");
  }
  async function restore(id: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const data = await browserAfdFetch<{
        conversation: History;
        messages: { kind: string; payload: Record<string, unknown> }[];
      }>(`assistant/conversations/${id}`);
      setThread(id);
      setShowHistory(false);
      setInput("");
      setUsed([]);
      setMode(data.conversation.mode);
      setProfile(data.conversation.profile_ref ?? "");
      setMessages(
        data.messages
          .filter((m) =>
            ["user", "assistant", "tool", "execution"].includes(m.kind),
          )
          .map((m) => ({
            kind: m.kind,
            text:
              typeof m.payload.text === "string" ? m.payload.text : undefined,
            tool:
              typeof m.payload.tool === "string" ? m.payload.tool : undefined,
            result:
              m.kind === "execution"
                ? m.payload
                : (m.payload.result as Record<string, unknown> | undefined),
          })),
      );
    } catch {
      setError("Conversation could not be loaded.");
    } finally {
      setBusy(false);
    }
  }
  function receive(event: AssistantEvent) {
    if (event.type === "conversation" && event.conversation_id)
      setThread(event.conversation_id);
    if (event.type === "delta")
      setMessages((old) => {
        const last = old.at(-1);
        return last?.kind === "assistant"
          ? [
              ...old.slice(0, -1),
              { ...last, text: (last.text ?? "") + event.text },
            ]
          : [...old, { kind: "assistant", text: event.text }];
      });
    if (event.type === "tool_start")
      setMessages((old) => [
        ...old,
        {
          kind: "progress",
          text: `${assistantToolLabel(event.tool)}…`,
        },
      ]);
    if (event.type === "tool_result")
      setMessages((old) => [
        ...old.filter((message) => message.kind !== "progress"),
        { kind: "tool", tool: event.tool, result: event.result },
      ]);
    if (event.type === "error")
      setError(
        event.code === "assistant_unavailable"
          ? "Assistant unavailable. Check server configuration or try again. Verify trading activity before retrying an action."
          : "The assistant reached its tool limit.",
      );
  }
  async function send() {
    if (!input.trim() || busy) return;
    const message = input.trim();
    setInput("");
    setError("");
    setBusy(true);
    setMessages((old) => [...old, { kind: "user", text: message }]);
    abort.current = new AbortController();
    let completed = false;
    try {
      const response = await fetch("/api/assistant/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abort.current.signal,
        body: JSON.stringify({
          conversation_id: thread || null,
          message,
          mode,
          context,
        }),
      });
      if (!response.ok || !response.body)
        throw new Error(
          (await response.json()).error || "Assistant could not start.",
        );
      await consumeAssistantStream(response.body, (event) => {
        if (event.type === "done" || event.type === "error") completed = true;
        receive(event);
      });
      if (!completed)
        throw new Error(
          "Response interrupted. Check activity before retrying an action.",
        );
    } catch (e) {
      setError(
        e instanceof Error && e.name !== "AbortError"
          ? e.message
          : "Response stopped. Already submitted actions may still finish; check Activity.",
      );
    } finally {
      setBusy(false);
      abort.current = null;
    }
  }
  async function approve(result: Record<string, unknown>) {
    const id = String(result.approval_id);
    setUsed((old) => [...old, id]);
    setBusy(true);
    setError("");
    try {
      const execution = await mutateAfd<Record<string, unknown>>(
        `assistant/conversations/${thread}/approvals/${id}`,
        "POST",
      );
      setMessages((old) => [...old, { kind: "execution", result: execution }]);
    } catch {
      setError(
        "Approval expired, was already used, or execution was blocked. Check Activity before retrying.",
      );
    } finally {
      setBusy(false);
    }
  }

  const modeLabel =
    mode === "research"
      ? "Research"
      : mode === "co_op"
        ? "Co-op"
        : "Autonomous";
  return (
    <>
      {!open && (
        <Button
          className="fixed bottom-6 right-6 z-40 h-12 w-12 gap-2.5 rounded-full p-0 sm:w-auto sm:px-5 shadow-lg shadow-emerald-950/15"
          onClick={() => setOpen(true)}
          aria-expanded={open}
          aria-label="Open assistant"
        >
          <Sparkles className="h-4 w-4" />
          <span className="hidden sm:inline">Assistant</span>
        </Button>
      )}
      {open && (
        <aside
          aria-label="Assistant"
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[460px] flex-col bg-card shadow-2xl ring-1 ring-border/60 sm:inset-y-4 sm:right-4 sm:w-[calc(100%_-_2rem)] sm:rounded-3xl"
        >
          <header className="flex items-center gap-3 px-6 pb-5 pt-6">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-emerald-500/10 text-emerald-600">
              <Sparkles className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold">Assistant</h2>
              <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                {selected?.nickname || selected?.broker || "Your workspace"} ·{" "}
                {context.market ?? assistantPageLabel(pathname)}
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded-full"
              disabled={busy}
              onClick={reset}
              aria-label="New chat"
            >
              <Plus className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded-full"
              aria-label="Close assistant"
              onClick={() => setOpen(false)}
            >
              <X className="h-4 w-4" />
            </Button>
          </header>
          <div className="mx-6 flex items-center justify-between border-y border-border/50 py-2">
            <Button
              variant="ghost"
              className="h-9 gap-2 rounded-full px-2 text-xs"
              aria-label="Change assistant mode"
              aria-expanded={showSettings}
              disabled={busy}
              onClick={() => {
                setShowSettings(!showSettings);
                setShowHistory(false);
              }}
            >
              <SlidersHorizontal className="h-3.5 w-3.5 text-muted-foreground" />
              {modeLabel}
              <span className="text-muted-foreground">
                · {assistantModes.find((item) => item.value === mode)?.detail}
              </span>
            </Button>
            <Button
              variant="ghost"
              className="h-9 gap-1.5 rounded-full px-2 text-xs text-muted-foreground"
              aria-label="Chat history"
              aria-expanded={showHistory}
              disabled={busy}
              onClick={() => {
                setShowHistory(!showHistory);
                setShowSettings(false);
              }}
            >
              <HistoryIcon className="h-3.5 w-3.5" />
              History
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {showSettings && (
              <AssistantOptions
                mode={mode}
                profile={profile}
                profiles={profiles.filter(
                  (item) => !selected || item.account_id === selected.publicRef,
                )}
                hasCurrentProfile={Boolean(
                  pageContext.profile_ref || pageProfile,
                )}
                disabled={busy}
                onMode={(value) => {
                  setMode(value);
                  reset();
                }}
                onProfile={(value) => {
                  setProfile(value);
                  reset();
                }}
              />
            )}
            {showHistory ? (
              <div className="px-6 py-6">
                <AssistantHistoryList
                  threads={threads}
                  current={thread}
                  loading={historyLoading}
                  error={historyError}
                  disabled={busy}
                  onSelect={(id) => void restore(id)}
                />
              </div>
            ) : (
              <>
                <div
                  className="flex-1 space-y-5 overflow-y-auto px-6 py-6"
                  role="log"
                  aria-live="polite"
                >
                  {!messages.length && (
                    <div className="flex min-h-72 flex-col justify-center py-8">
                      <span className="mb-5 grid h-12 w-12 place-items-center rounded-full bg-emerald-500/10 text-emerald-600">
                        <Bot className="h-6 w-6" />
                      </span>
                      <h3 className="text-2xl font-medium tracking-tight">
                        A second pair of eyes.
                      </h3>
                      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                        Explore a market, review your account, or think through
                        a strategy.
                      </p>
                      <div className="mt-6 space-y-2">
                        {[
                          "What can you help me with?",
                          "Summarize my account setup",
                          "Help me review my strategy",
                        ].map((prompt) => (
                          <button
                            key={prompt}
                            type="button"
                            className="flex w-full items-center justify-between gap-3 rounded-2xl bg-muted/50 px-4 py-3 text-left text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                            onClick={() => setInput(prompt)}
                          >
                            {prompt}
                            <ArrowUpRight className="h-3.5 w-3.5 shrink-0" />
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {messages.map((m, index) => (
                    <article
                      key={index}
                      className={`text-sm leading-relaxed ${m.kind === "user" ? "ml-8 rounded-2xl rounded-br-md bg-emerald-500/10 px-4 py-3" : m.kind === "tool" || m.kind === "execution" ? "rounded-2xl bg-muted/50 p-4" : m.kind === "progress" ? "text-xs text-muted-foreground" : "px-1 py-1"}`}
                    >
                      {m.text && (
                        <p className="whitespace-pre-wrap break-words">
                          {m.text}
                        </p>
                      )}
                      {m.result && (
                        <>
                          <AssistantResult
                            result={m.result}
                            title={
                              m.kind === "execution"
                                ? "Execution result"
                                : assistantToolLabel(m.tool)
                            }
                          />
                          {m.result.status === "approval_required" && (
                            <Button
                              className="mt-3 w-full"
                              disabled={
                                busy ||
                                used.includes(String(m.result.approval_id)) ||
                                mode !== "co_op"
                              }
                              onClick={() => void approve(m.result!)}
                            >
                              Approve{" "}
                              {String(m.result.action).replaceAll("_", " ")}
                            </Button>
                          )}
                        </>
                      )}
                    </article>
                  ))}
                  <div ref={scroll} />
                </div>
              </>
            )}
          </div>
          {error && (
            <p
              role="alert"
              className="mx-6 mb-3 rounded-xl bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300"
            >
              {error}
            </p>
          )}
          {!showHistory && (
            <form
              className="p-4 pt-0 sm:p-5 sm:pt-0"
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <div className="flex items-end gap-2 rounded-2xl bg-background p-3 ring-1 ring-border/70 focus-within:ring-emerald-500/50">
                <textarea
                  aria-label="Message to assistant"
                  value={input}
                  maxLength={8000}
                  onChange={(e) => setInput(e.target.value)}
                  className="min-h-16 flex-1 resize-none bg-transparent px-1 py-1 text-sm outline-none placeholder:text-muted-foreground"
                  placeholder="What’s on your mind?"
                  disabled={busy}
                />
                {busy ? (
                  <Button
                    type="button"
                    variant="secondary"
                    size="icon"
                    className="h-9 w-9 rounded-full"
                    aria-label="Stop response"
                    onClick={() => abort.current?.abort()}
                  >
                    <Square className="h-3.5 w-3.5" />
                  </Button>
                ) : (
                  <Button
                    aria-label="Send message"
                    size="icon"
                    className="h-9 w-9 rounded-full"
                    disabled={
                      !input.trim() || (mode !== "research" && !boundProfile)
                    }
                  >
                    <Send className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
              <p className="mt-2.5 text-center text-[10px] text-muted-foreground">
                {mode === "research"
                  ? "Research mode cannot place trades."
                  : "Your profile permissions and safety controls always apply."}
              </p>
            </form>
          )}
        </aside>
      )}
    </>
  );
}
