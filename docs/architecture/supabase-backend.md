# Supabase backend

The complete application remains in `agentic-trading-dashboard`. Next.js calls its private Python engine through `DESK_ENGINE_URL`. The retired backend and Neon are not used. There is no application login; the fixed shared workspace, mode limits, exact-action approvals, demo restrictions, kill switch and risk checks remain.

## Production persistence

`STORAGE_BACKEND=postgres` selects the server-only `SUPABASE_DB_URL` connection. Use the transaction-pooler URL from Supabase's Connect panel, with TLS (`sslmode=require` or certificate verification). The driver does not create named prepared statements. It opens short-lived transactions, sets a restricted runtime role and a transaction-local search path, and closes every connection. Statement and lock timeouts bound database work. Configuration and connection errors returned to clients never include the connection string.

Every durable consumer uses the same database boundary: brokers/accounts/profiles, previews/executions/controls, schedules/leases/heartbeats, settings/preferences/watchlists, agent history/approvals, signal intents, candle cache and request cooldowns. There is no production SQLite fallback. `VERCEL=1` makes the default app environment production; `/ready` checks storage and schema version. Remote schema initialization is never performed on a user request.

The limited SQL compatibility boundary retains established repository contracts while adapting placeholders, case-insensitive comparisons, insert-ignore, generated IDs and scalar expressions to Postgres. Savepoints preserve duplicate-claim handling without leaving a transaction aborted. PostgreSQL advisory transaction locks protect the existing explicit lease/control critical sections. Approval and conversation-turn claims remain conditional single-statement updates.

## Deployable schema

`supabase/schema.sql` is the current fresh-install schema: 32 product/runtime tables plus one schema-version table, built-in strategy templates, constraints and indexes. It supersedes the earlier 19-table design proposal for this release. It contains no imported users, accounts, credentials or SQLite rows. Monetary/quantity/risk columns use fixed-scale NUMERIC; repository responses retain the existing numeric Python/JSON contract. Epoch leases use double precision and existing ISO timestamp contracts remain text in this compatibility release.

Everything lives in the private `desk` schema. PUBLIC has no schema access. Every table has RLS enabled; only the NOLOGIN `desk_runtime` role gets product data access. Its policy deliberately permits the shared server workspace; it is not a claim of user authentication or per-user RLS. Browser roles have no grants or policies. The server database login must be allowed to SET ROLE desk_runtime. Do not expose `desk` through Supabase's Data API and do not place database/service credentials in NEXT_PUBLIC variables.

The historical `users.auth0_sub` column retains its identifier for SQL compatibility only. It stores `WORKSPACE_OWNER_SUB`; Auth0 is not involved.

## Fresh installation

Create/select a separate Supabase project in the intended organization, then set engine environment variables:

- `APP_ENV=production`, `STORAGE_BACKEND=postgres`
- `SUPABASE_DB_URL`: server-only transaction-pooler DSN with TLS
- `BROKER_SECRET_KEY` or versioned encryption keys: persistent secret used only by the engine
- `WORKSPACE_OWNER_SUB=shared-workspace`
- `OPENAI_API_KEY` and `AGENT_MODEL` for assistant responses
- Keep `KILL_SWITCH_ENABLED=true`, `LIVE_TRADING_ENABLED=false`, `AUTONOMOUS_SCHEDULER_EMBEDDED=false` during verification

From `server/`, run `python -m scripts.install_postgres` using the deployment's database configuration. The installer refuses any existing `desk` schema and rolls back on errors. It does not open SQLite and does not use the legacy DATABASE_URL variable. Alternatively apply the checked-in SQL through Supabase's schema workflow. Schema creation and role creation require the database owner's credentials. Runtime sets the restricted role before queries.

Deploy the single Vercel project with its private engine binding. Verify `/ready` internally before exercising the desk. Broker account creation is a separate user operation; no credentials or data are carried over automatically.

Persistent scheduled execution remains a separate worker concern: the worker can now share Postgres state, but no worker or trading session is started by schema installation or this change.

## Verification

The Python suite exercises the existing local development contracts. `tests/postgres` exercises the fresh schema and real Postgres SQL through psycopg2: encryption/key versions, account ownership, profiles, preferences, case-insensitive uniqueness, approvals/replays, controls, settings, idempotent signals, scheduler leases, cooldowns, candle eviction and API reads/writes without login.

Local PGlite tests use one underlying PostgreSQL WASM connection; they do not establish native multi-session behavior. `.github/workflows/backend.yml` supplies an isolated PostgreSQL 17 service and runs independent connections, including concurrent approval claims. Hosted Supabase and Vercel verification must be reported separately from these tests.

No hosted project was changed while the selected organization was inaccessible. No SQLite data migration, Neon access or trade execution is part of this implementation.

References: [Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres), [pooler prepared statements](https://supabase.com/docs/guides/troubleshooting/disabling-prepared-statements-qL8lEL).

## Additive Operations upgrade

For an existing installation, apply `supabase/migrations/20261031025418_operations_events.sql` before deploying the Operations journal code. Do not rerun the fresh installer against an existing `desk` schema. The new private table records safe profile, schedule, and connection changes in the same transaction as the change. Run history, control audits, and broker executions remain in their existing tables. Journal reads are owner-scoped at the server boundary.

The inbox uses notification preferences from Postgres and a read cursor stored in the current browser. It polls visible pages every 30 seconds; it does not send emails or claim to run a background daily-summary service. Workspace JSON export excludes credentials, API keys, conversations, and model prompts. CSV exports prefix formula-like values before quoting.

## Demo position protection

Apply `supabase/migrations/20261031031952_position_management.sql` before deploying the position-management worker. Its private state table serializes a position's protection writes across independent workers. Claims, immutable action previews and action records commit together. An unconfirmed broker write has no automatic lease expiry or write retry; later reads must observe the requested stop or stronger protection before the position can be managed again. Closed positions end protection management without claiming that an unconfirmed stop was applied.

Only the explicit self-hosted `--execute-demo` mode invokes management. Shadow workers cannot modify positions. Each change requires an enabled demo-autonomous profile, enabled demo autonomy, an inactive kill switch, fresh account ownership checks, a verified submission-to-position link, and a broker stop/side/instrument read. Management skips ambiguous netted entries, unverifiable stop data, and foreign positions. It changes only `stopLoss`, preserves existing take-profit orders, and tightens protection. Break-even activation and trailing activation use the original entry-to-stop distance; trailing distances and break-even offsets are price units. Changes appear in Activity.

The server applies fixed-distance, percentage and stop policies based on twice the hourly ATR before sizing. Reward targets include spread in the configured net reward-to-risk ratio. Broker tick rounding, minimum distances, all original risk checks and fresh submission checks still apply. Initial stop and target protection remain mandatory. Partial exits are not advertised as supported. Broker endpoint references: https://public-api.tradelocker.com/reference/modifyposition and https://public-api.tradelocker.com/reference/closeposition .

Current demo order execution is limited to EURUSD, GBPUSD, AUDUSD, NZDUSD and USDCAD. The strategy selector exposes this supported set, independently of the broker's larger catalogue and Kraken crypto research feed. Stable instrument IDs and legacy symbols are resolved through the owned broker catalogue, and exclusions remain enforced.

A strategy margin cap tightens the allocation margin budget. Correlated-risk caps conservatively treat all open profile risk as correlated. Gross-exposure caps include new account-currency notional and require verifiable account-currency exposure for existing positions; missing provider data blocks the request. Autonomous entries retain mandatory news validation; the Co-op news switch enables the additional manual check and cannot override a strategy's mandatory provider requirements.

Adaptive demo sizing scales the saved base risk by validated decision confidence divided by the minimum confidence (factor bounded to 0.5–1.5), then clamps to the saved minimum/maximum and the hard 1% ceiling. The immutable preview records confidence and authorized risk. Submission recomputes the bound against current policy without increasing the reviewed quantity. Co-op sizing retains the fixed/manual risk bound.

The hourly-forex preset independently requires aligned SMA20/SMA50 trends and closes on the trend side of SMA20 on D1, H4 and H1, plus directional M15 momentum and a close on the same side of M15 SMA20. Missing or conflicting data blocks an AI trade candidate. Adaptive analysis uses the bounded model comparison without this preset gate, retaining all permission and risk checks. These are demo software rules, with no claim of strategy performance.

Market-aware schedules skip checks outside conservative market hours before requesting an AI decision; disabling that option still retains the broker's mandatory tradability checks before any order. Nonexistent daylight-saving clock times are skipped, and a repeated clock time runs once rather than twice.
