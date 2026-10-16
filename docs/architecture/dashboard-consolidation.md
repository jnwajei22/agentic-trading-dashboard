# Dashboard consolidation

This supersedes the earlier split-repository architecture. The user clarified that `agentic-trading-app` and its MCP deployment are retired and that the complete product belongs in `agentic-trading-dashboard`. No files in the retired repository were edited in this correction.

## Runtime

Browser → dashboard Next.js pages and allowlisted same-origin routes → private engine service from this repository → native agent/trading/risk/storage services. OpenAI is called only by the engine. There is no Auth0 browser session, access-token acquisition, MCP ingress, OAuth ingress, or legacy external-backend URL fallback.

The existing Python engine was imported instead of rewriting risk/execution logic into TypeScript. Its source, requirements, tests, fixtures, operational scripts, and PostgreSQL proposal now live in this repository. Its public project/deployment is retired; Python remains an internal runtime component of the dashboard application.

`vercel.json` declares Vercel Services (beta) with a private binding from dashboard to engine and only dashboard public ingress. Binding URLs are available at runtime. Streaming and server-rendered requests use the same binding. Local development runs the engine on loopback and Next.js on port 3000.

## Shared workspace

`current_claims` now supplies a fixed backend-configured owner and read/prepare/execute capabilities. These are shared-operator capabilities, not authenticated user permissions. Incoming bearer tokens, owner headers, and owner query parameters do not choose the identity. `WORKSPACE_OWNER_SUB` can explicitly retain an existing owner's scope without changing rows. Default is a new shared workspace; existing other-owner rows remain inaccessible and unchanged.

Research remains read-only; Co-op retains expiring exact-action human approvals; Autonomous retains profile binding, allowed actions, valid session, demo account, kill-switch, risk, and dispatch-time checks. Conversation ownership and approval replay/expiry checks remain. No mode control was removed to bypass login.

Root and legacy `/login` redirect directly to the desk. Sign-out and authenticated-email UI are removed. TradeLocker login remains a broker credential flow, separate from application login.

## Storage and deployment limits

No live database was read or migrated. No Neon connection, SQL schema application, production configuration changes, scheduled worker start, or trade execution occurred. SQLite remains authoritative. The provided Vercel service layout does not solve durable SQLite storage: stateless function disk is unsuitable for trading state. Persistent storage/hosting or a separately authorized persistence implementation is still needed before production operation. PostgreSQL remains proposal only.

Prior architecture reports are retained as historical records with a supersession notice. Their old Auth0/split-service statements do not describe the current implementation.

## Validation

- Internal engine: 349 passed, 1 opt-in broker test skipped; one dependency deprecation warning.
- Frontend: 81 passed; TypeScript noEmit passed; production Next.js build passed.
- ESLint: no errors; three existing warnings (two unused bindings, existing option aria-selected).
- Local production HTTP flow: root/login redirect to dashboard; dashboard renders shared access; `/api/afd/me` and conversation history return 200 without tokens; foreign-origin mutation returns 403; same-origin watchlist creation returns 201 and is read back from a temporary SQLite file; assistant stream forwards SSE with a sanitized missing-provider event.
- Mocked agent integration tests continue to cover normal streamed model output, tools, mode limits, exact-action approvals, expiry/replay and dispatch guards. No live OpenAI or broker request was used.
- Tracked-file secret scan and whitespace check passed.
- Hosted Vercel deployment, private service binding and visual browser interaction are not verified. The Vercel connection returned a project-scope 403 on the earlier deployment logs; the CLI was unavailable.
- The temporary local database was disposable. No production SQLite or Neon connection was made, no existing rows were migrated, no worker was started, and no trade was placed.
