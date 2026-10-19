import { formatEnum } from "./display";

export const assistantModes = [
  {
    value: "research",
    label: "Research",
    detail: "Read-only",
    description: "Explore markets and ask questions. No trading actions.",
  },
  {
    value: "co_op",
    label: "Co-op",
    detail: "Approval required",
    description: "Prepare actions for you to review and approve.",
  },
  {
    value: "autonomous",
    label: "Autonomous",
    detail: "Strategy limits",
    description: "Use an authorized strategy and its existing safety controls.",
  },
] as const;

export type AssistantHistory = {
  id: string;
  mode: string;
  profile_ref: string | null;
  title?: string;
  created_at?: number;
};

export function assistantPageLabel(pathname: string) {
  const [section, detail] = pathname.split("/").filter(Boolean);
  if (section === "autonomous" && detail) return "Strategy details";
  return (
    (
      {
        dashboard: "Overview",
        markets: "Markets",
        trade: "Trading",
        autonomous: "Strategies",
        schedules: "Schedules",
        activity: "Activity",
        settings: "Settings",
      } as Record<string, string>
    )[section] ?? "Workspace"
  );
}

export function assistantToolLabel(name?: string) {
  return formatEnum(
    (name ?? "Action").replace(/^(?:list_my_|list_|get_|render_|search_)/, ""),
  )
    .replace(/Tradelocker/g, "TradeLocker");
}
