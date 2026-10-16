# Supabase backend

The complete application remains in `agentic-trading-dashboard`. Next.js calls its private Python engine through `DESK_ENGINE_URL`. The retired backend and Neon are not used. There is no application login; the fixed shared workspace, mode limits, exact-action approvals, demo restrictions, kill switch and risk checks remain.

## Production persistence

`STORAGE_BACKEND=postgres` selects the server-only `SUPABASE_DB_URL` connection. Use the transaction-pooler URL from Supabase's Connect panel, with TLS (`sslmode=require` or certificate verification). The driver does not create named prepared statements. It opens short-lived transactions, sets a restricted runtime role and a transaction-local search path, and closes every connection. Statement and lock timeouts bound database work. Configuration and connection errors returned to clients never include the connection string.

Every durable consumer uses the same database boundary: brokers/accounts/profiles, previews/executions/controls, schedules/leases/heartbeats, settings/preferences/watchlists, agent history/approvals, signal intents, candle cache and request cooldowns. There is no production SQLite fallback. `VERCEL=1` makes the default app environment production; `/ready` checks storage and schema version. Remote schema initialization is never performed on a user request.

The limited SQL compatibility boundary retains established repository contracts while adapting placeholders, case-insensitive comparisons, insert-ignore, generated IDs and scalar expressions to Postgres. Savepoints preserve duplicate-claim handling without leaving a transaction aborted. PostgreSQL advisory transaction locks protect the existing explicit lease/control critical sections. Approval and conversation-turn claims remain conditional single-statement updates.

## Deployable schema

`supabase/schema.sql` is the current fresh-install schema: 30 product/runtime tables plus one schema-version table, built-in strategy templates, constraints and indexes. It supersedes the earlier 19-table design proposal for this release. It contains no imported users, accounts, credentials or SQLite rows. Monetary/quantity/risk columns use fixed-scale NUMERIC; repository responses retain the existing numeric Python/JSON contract. Epoch leases use double precision and existing ISO timestamp contracts remain text in this compatibility release.

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
