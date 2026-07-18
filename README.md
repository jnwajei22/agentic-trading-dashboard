# Agentic Trading Desk

Standalone Next.js web client for the existing Agentic Forex Desk platform. It provides authenticated TradeLocker onboarding, account and execution-profile management, broker market discovery, autonomous safety controls, durable schedules, and structured audit activity without creating a second backend or source of truth.

## Architecture

```text
Browser
  │ same-origin session + JSON only
  ▼
Vercel / Next.js 15
  ├─ Auth0 server session
  ├─ protected React server routes
  └─ /api/afd/[...path] strict method/path allowlist
       │ Authorization: Bearer <server-acquired token>
       ▼
Agentic Forex Desk FastAPI service (agentic-forex-app)
  ├─ database-backed Auth0 identity
  ├─ encrypted TradeLocker connections and accounts
  ├─ execution profiles and risk rules
  ├─ autonomous controls, decisions, and audits
  └─ durable scheduler worker (Raspberry Pi or hosted service)
```

`agentic-trading-dashboard` is a separate deployable client. `agentic-forex-app` remains authoritative for identity, credentials, broker state, risk, execution, schedules, and worker state. MCP and this application are separate authenticated clients of that same backend. The browser never calls FastAPI or MCP directly.

## Local setup

Requirements: Node.js 20+, pnpm, an Auth0 Regular Web Application, and a reachable Agentic Forex Desk backend.

```powershell
Copy-Item .env.example .env.local
pnpm install
pnpm dev
```

Required variables:

- `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, and a cryptographically random `AUTH0_SECRET`
- `APP_BASE_URL`, normally `http://localhost:3000`
- `AUTH0_AUDIENCE`, exactly matching the FastAPI `AUTH_AUDIENCE`
- `BACKEND_API_BASE_URL`, the FastAPI origin consumed only by Next.js server code. `NEXT_PUBLIC_API_BASE_URL` remains a deprecated compatibility fallback.

Production backend URLs must be HTTPS, must not be localhost, and must not point back to the Vercel frontend. Never place Auth0 client secrets, TradeLocker credentials, encryption secrets, access tokens, or OpenAI keys in browser state or storage.

## Auth0 configuration

For local development add:

- Allowed callback URL: `http://localhost:3000/auth/callback`
- Allowed logout URL: `http://localhost:3000`
- Allowed web origin: `http://localhost:3000`

Repeat these entries with the stable production origin. Preview deployments need explicitly registered callbacks or a controlled preview-domain strategy. The requested audience must issue the backend scopes `forex:read forex:preview forex:execute`.

## Vercel and backend deployment

Import this repository as its own Vercel project and configure the small bootstrap set in `.env.example`. Set `APP_BASE_URL` to the stable Vercel domain and `BACKEND_API_BASE_URL` to the public HTTPS FastAPI origin. A Raspberry Pi backend can remain private behind Cloudflare Tunnel; the tunnel origin is the backend URL, while the persistent autonomous worker continues running beside the backend. Vercel never starts or owns the scheduler worker.

## Provider boundaries

The UI consumes normalized `/api/trading/*` and `/api/providers` contracts through the authenticated same-origin proxy. Broker, platform, execution, chart, signal, and market-data roles are displayed separately. TradingView is chart/signal infrastructure, not a broker, and its chart prices are never execution-authoritative. Robinhood Agentic remains visibly unconfigured until an official MCP/OAuth integration is implemented and tested. Users never enter deployment environment variables or platform API keys.

Markets use canonical instrument IDs in URL and watchlist state. The backend maps each canonical ID independently for TradingView, Finnhub, and the selected execution provider; raw provider symbols are not interchangeable.

The backend should allow the stable frontend origin where its deployment configuration requires it. Browser requests themselves remain same-origin to Vercel, and only the server proxy forwards bearer tokens.

## Supported web features

- Auth0 login/logout and protected routes
- TradeLocker credential onboarding, discovery, selection, reauthentication, and connection/account settings
- Operational dashboard with partial endpoint failure isolation
- Broker instrument search, filters, groups, and profile-universe context
- Backend-confirmed kill switch and demo/live autonomous controls with rollback and live confirmation
- Execution profile creation, enable/disable, exact-name deletion, V2 editing, capabilities, and validation
- Durable schedule create/edit/pause/resume/delete and backend-approved safe retry
- Autonomous runs, run audits, demo executions, and safety-control audit history
- Responsive accessible navigation and light/dark themes

## Explicitly unsupported web features

The current backend has no authenticated web REST contracts for canonical TradeLocker candles, open positions, pending orders, order previews, manual submission, or order history. The UI marks those areas unavailable and never fabricates data, calls MCP, or substitutes TradingView/Finnhub values for execution. TradingView is mentioned only as non-authoritative visual context. Live autonomy also remains disabled unless the backend explicitly reports support.

## Security boundaries

- `/api/afd/[...path]` rejects paths and methods outside its explicit allowlist with 404.
- Auth0 access tokens are acquired on the server and forwarded only to the validated backend origin.
- Requests use a ten-second timeout, `cache: no-store`, status preservation, and sanitized JSON errors.
- Tokens, cookies, credentials, and request bodies are never logged.
- Unknown operational state defaults to blocked or unavailable; safety mutations require backend confirmation.
- TradeLocker passwords are submitted through the same-origin proxy and never stored in URLs, browser storage, or client-readable cookies.

## Quality commands

```powershell
pnpm lint
pnpm test
pnpm build
```
