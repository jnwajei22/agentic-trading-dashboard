# Agentic Trading Dashboard

This repository owns the complete trading desk: the Next.js interface, same-origin API routes, native AI assistant, and an internal Python trading engine under `server/`. It has no runtime dependency on `agentic-trading-app` or the former MCP deployment.

There is **no login system**. Every visitor uses the same server-selected workspace. The browser cannot choose an owner. Mode restrictions, ownership checks, co-op approvals, demo-only autonomous execution, kill switch, and risk controls remain enforced by the internal engine. Anyone who can reach the desk can operate that shared workspace.

## Run locally

Requires Node.js 20+, pnpm, and Python 3.11+.

```bash
pnpm install
python -m venv server/.venv
server/.venv/bin/pip install -r server/requirements.txt
cp server/.env.example server/.env
# For local SQLite development, set STORAGE_BACKEND=sqlite in server/.env.
cd server
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal at the repository root:

```bash
pnpm dev
```

Open `http://localhost:3000`. The local engine defaults to loopback port 8000. No Auth0 variables or retired backend URL are used. Configure `BROKER_SECRET_KEY` in `server/.env` before connecting a broker. Set `AGENT_MODEL` and `OPENAI_API_KEY` there to enable model responses. No browser receives those secrets.

`WORKSPACE_OWNER_SUB` defaults to `shared-workspace`. An operator may explicitly set it to an existing owner identifier to reuse that owner's records on an existing local database. This does not move, merge, discover, or reassign any data. A new empty database creates an empty workspace.

## One deployment project

`vercel.json` defines a Next.js dashboard and a private Python engine in the **same project** using Vercel Services (beta). All public ingress goes to Next.js. `DESK_ENGINE_URL` is injected by the private service binding; do not set it to the retired deployment. Next.js retains allowlisted API routes, same-origin mutation checks, server rendering, and unbuffered streaming. The engine has no public ingress rewrite and no browser CORS allowance.

**Production persistence uses Supabase Postgres:** set `STORAGE_BACKEND=postgres` and the server-only `SUPABASE_DB_URL`, install the fresh private schema, and verify readiness. Production refuses SQLite. See [Supabase deployment instructions](docs/architecture/supabase-backend.md). Hosted deployment is pending account/project access; Neon remains untouched and no SQLite data is migrated.

Embedded scheduling defaults off. Continuous scheduled runs need a persistent worker using the same Postgres database; the worker code is included under `server/` and has not been started.

## Verify

```bash
pnpm test
pnpm exec tsc --noEmit
pnpm lint
pnpm build
cd server
.venv/bin/python -m pytest -q
```

Tests use mocks/temporary databases. The opt-in real broker test remains skipped. Do not run live execution to verify this change.

See [consolidation details](docs/architecture/dashboard-consolidation.md) and the [current Postgres installation schema](supabase/schema.sql). The earlier PostgreSQL proposal is historical.
