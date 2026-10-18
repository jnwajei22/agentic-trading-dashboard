import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Bot,
  CalendarClock,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import { workspaceBackendClient } from "@/lib/afd/backend";
import { loadDashboardData } from "@/lib/afd/dashboard-data";
import { accountContext, formatEnum } from "@/lib/afd/display";
import { automationStatus } from "@/lib/afd/automation";
import { PageHeader } from "@/components/desk/page-header";
import { OnboardingChecklist } from "@/components/desk/onboarding-checklist";
import { StatusPill } from "@/components/desk/status-pill";
import { Button } from "@/components/ui/button";
export const metadata = { title: "Overview" };
const when = (value?: string) =>
  value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "Not recorded";
export default async function DashboardPage() {
  let data;
  try {
    data = await loadDashboardData(await workspaceBackendClient());
  } catch {
    data = await loadDashboardData(async () => {
      throw new Error("unavailable");
    });
  }
  const selected = data.accounts.find((account) => account.is_default_analysis);
  const active = data.schedules.filter((schedule) => schedule.enabled);
  const enabledProfiles = data.profiles.filter(
    (profile) => profile.enabled,
  ).length;
  const automation = automationStatus(data);
  const steps = [
    {
      label: "Add a trading connection",
      href: "/connect-tradelocker?new=1",
      complete: data.connections.some((connection) => connection.enabled),
    },
    {
      label: "Select a trading account",
      href: "/settings/accounts",
      complete: Boolean(selected),
    },
    {
      label: "Create a strategy",
      href: "/autonomous?create=1",
      complete: data.profiles.length > 0,
    },
    {
      label: "Configure risk",
      href: data.profiles[0]
        ? `/autonomous/${data.profiles[0].public_id}`
        : "/autonomous",
      complete: data.profiles.some((profile) =>
        Boolean(profile.profile_v2?.risk_policy),
      ),
    },
    {
      label: "Create a schedule",
      href: "/schedules?create=1",
      complete: data.schedules.length > 0,
    },
    {
      label: "Run a demo test",
      href: "/autonomous",
      complete: data.runs.some(
        (run) => run.dry_run || run.trigger_reason === "demo_test",
      ),
    },
  ];
  const activity = [
    ...data.runs.slice(0, 5).map((run) => ({
      id: run.run_id,
      title: formatEnum(run.outcome),
      detail: run.chosen_instrument ?? "Autonomous decision",
      time: run.started_at,
    })),
    ...data.executions.slice(0, 5).map((item) => ({
      id: item.id,
      title: formatEnum(item.action_type ?? item.symbol ?? "Demo execution"),
      detail: formatEnum(item.state ?? item.result),
      time: item.created_at,
    })),
  ]
    .sort(
      (a, b) =>
        (Date.parse(b.time ?? "") || 0) - (Date.parse(a.time ?? "") || 0),
    )
    .slice(0, 5);
  const outcomeTotal = Object.values(data.daily.outcomes).reduce(
    (sum, count) => sum + Number(count),
    0,
  );
  return (
    <div className="space-y-7">
      <PageHeader
        eyebrow="Your workspace"
        title="Overview"
        description="A little clarity before your next move."
        actions={
          <StatusPill
            label={
              data.coreUnavailable ? "Connection interrupted" : "Desk connected"
            }
            tone={data.coreUnavailable ? "bad" : "good"}
          />
        }
      />
      {Object.keys(data.errors).length > 0 && (
        <div
          role="status"
          className="flex gap-3 rounded-2xl bg-amber-500/10 px-5 py-4 text-sm"
        >
          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
          <p>
            Some operational data is unavailable.{" "}
            <span className="text-muted-foreground">
              Loaded sections are preserved; unavailable actions remain blocked.
            </span>
          </p>
        </div>
      )}
      <section
        aria-label="Workspace summary"
        className="grid grid-cols-2 divide-border/50 rounded-3xl bg-card shadow-sm ring-1 ring-border/50 sm:grid-cols-3 sm:divide-x"
      >
        <Metric
          className="col-span-2 border-b border-border/50 sm:col-span-1 sm:border-b-0"
          icon={Wallet}
          label="Primary account"
          value={
            selected
              ? selected.nickname ||
                `${selected.broker_name || "Trading"} #${selected.account_number}`
              : "No account selected"
          }
          detail={
            selected
              ? accountContext(selected)
              : "Connect your broker to get started"
          }
        />
        <Metric
          className="border-r border-border/50 sm:border-r-0"
          icon={Bot}
          label="Active strategies"
          value={String(enabledProfiles)}
          detail={`${data.profiles.length} execution profiles configured`}
        />
        <Metric
          icon={CalendarClock}
          label="Scheduled sessions"
          value={String(active.length)}
          detail={`${data.schedules.length} schedules configured`}
        />
      </section>
      <OnboardingChecklist steps={steps} />
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.8fr)_minmax(280px,1fr)]">
        <div className="space-y-6">
          <section
            className="desk-surface p-6 sm:p-8"
            aria-labelledby="today-heading"
          >
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="desk-eyebrow">Daily snapshot</p>
                <h2
                  id="today-heading"
                  className="mt-2 text-xl font-semibold tracking-tight"
                >
                  Today at your desk
                </h2>
              </div>
              <span className="grid h-10 w-10 place-items-center rounded-full bg-emerald-500/10 text-emerald-600">
                <Activity className="h-5 w-5" />
              </span>
            </div>
            {outcomeTotal === 0 ? (
              <div className="py-10 sm:py-12">
                <p className="text-3xl font-medium tracking-tight sm:text-4xl">
                  A clear slate.
                </p>
                <p className="mt-3 max-w-md text-sm leading-relaxed text-muted-foreground">
                  No outcomes recorded today. Your decisions and executions will
                  appear here as your desk gets to work.
                </p>
              </div>
            ) : (
              <div className="py-9">
                <p className="text-4xl font-medium tabular-nums">
                  {outcomeTotal}
                  <span className="ml-3 text-sm font-normal text-muted-foreground">
                    outcomes today
                  </span>
                </p>
              </div>
            )}
            <dl className="grid grid-cols-3 gap-x-4 gap-y-5 border-t border-border/50 pt-5 sm:grid-cols-6">
              {Object.entries(data.daily.outcomes).map(([key, count]) => (
                <div key={key}>
                  <dt className="text-[11px] text-muted-foreground">
                    {formatEnum(key)}
                  </dt>
                  <dd className="mt-2 text-lg font-medium tabular-nums">
                    {count}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
          <section
            className="desk-surface p-6 sm:p-8"
            aria-labelledby="activity-heading"
          >
            <div className="mb-5 flex items-center justify-between">
              <h2 id="activity-heading" className="text-base font-semibold">
                Recent activity
              </h2>
              <Link
                href="/activity"
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                View all
                <ArrowUpRight className="h-3.5 w-3.5" />
              </Link>
            </div>
            {activity.length ? (
              <div className="divide-y divide-border/50">
                {activity.map((item) => (
                  <Link
                    key={item.id}
                    href="/activity"
                    className="flex items-center gap-4 py-4"
                  >
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-muted">
                      <Activity className="h-4 w-4 text-muted-foreground" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <strong className="block text-sm font-medium">
                        {item.title}
                      </strong>
                      <span className="block truncate text-xs text-muted-foreground">
                        {item.detail}
                      </span>
                    </span>
                    <time className="text-right text-[11px] text-muted-foreground">
                      {when(item.time)}
                    </time>
                  </Link>
                ))}
              </div>
            ) : (
              <div className="flex items-center gap-4 py-5">
                <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-muted">
                  <Activity className="h-5 w-5 text-muted-foreground" />
                </span>
                <div>
                  <p className="text-sm font-medium">Nothing to catch up on</p>
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    Autonomous runs and demo executions will appear here.
                  </p>
                </div>
              </div>
            )}
          </section>
        </div>
        <div className="space-y-6">
          <section
            className="desk-surface p-6 sm:p-7"
            aria-labelledby="safety-heading"
          >
            <span className="mb-5 grid h-11 w-11 place-items-center rounded-2xl bg-emerald-500/10 text-emerald-600">
              <ShieldCheck className="h-5 w-5" />
            </span>
            <h2 id="safety-heading" className="text-base font-semibold">
              Safety & automation
            </h2>
            <div className="mt-5 flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                Global kill switch
              </span>
              <StatusPill
                label={
                  data.controls.global_autonomous_kill_switch
                    ? "Enabled"
                    : "Disabled"
                }
                tone={
                  data.controls.global_autonomous_kill_switch
                    ? "warn"
                    : "neutral"
                }
              />
            </div>
            <div className="mt-5 border-t border-border/50 pt-5">
              <p className="text-sm font-medium">{automation.state}</p>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                {automation.message}
              </p>
            </div>
            <Button
              asChild
              variant="ghost"
              className="mt-5 h-auto justify-start px-0 text-xs hover:bg-transparent"
            >
              <Link href="/autonomous">
                Review automation
                <ArrowUpRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </section>
          <section
            className="desk-surface p-6 sm:p-7"
            aria-labelledby="schedule-heading"
          >
            <div className="flex items-center justify-between">
              <h2 id="schedule-heading" className="text-base font-semibold">
                Coming up
              </h2>
              <CalendarClock className="h-4 w-4 text-muted-foreground" />
            </div>
            {active.length ? (
              <div className="mt-4 divide-y divide-border/50">
                {active.slice(0, 4).map((schedule) => {
                  const profile = data.profiles.find(
                    (item) => item.public_id === schedule.profile_ref,
                  );
                  const name = profile?.name?.trim();
                  const profileName =
                    name && !name.toLowerCase().startsWith("profile_")
                      ? name
                      : "Execution profile";
                  return (
                    <Link
                      key={schedule.id}
                      href="/schedules"
                      className="block py-3"
                    >
                      <p className="text-sm font-medium">{profileName}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {when(schedule.next_run_at)}
                      </p>
                    </Link>
                  );
                })}
              </div>
            ) : (
              <div className="py-6">
                <p className="text-sm font-medium">Your calendar is open</p>
                <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                  No active schedules. Set a rhythm when your strategy is ready.
                </p>
              </div>
            )}
            <Link
              href="/schedules"
              className="flex items-center gap-1 text-xs text-emerald-700 hover:underline dark:text-emerald-300"
            >
              Manage schedules
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
          </section>
        </div>
      </div>
    </div>
  );
}
function Metric({
  icon: Icon,
  className = "",
  label,
  value,
  detail,
}: {
  icon: typeof Bot;
  className?: string;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className={`min-w-0 p-5 sm:p-7 ${className}`}>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <p
        className="mt-4 truncate text-2xl font-medium tracking-tight tabular-nums"
        title={value}
      >
        {value}
      </p>
      <p className="mt-2 truncate text-xs text-muted-foreground">{detail}</p>
    </div>
  );
}
