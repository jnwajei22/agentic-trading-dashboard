# Self-hosted worker

The hosted Next.js dashboard and private FastAPI engine remain on Vercel. A separately running worker on your computer or server uses the **same Postgres database** and broker encryption key. It needs outbound HTTPS and a TLS database connection; it exposes no listening ports and needs no router forwarding or public tunnel.

## Start in shadow mode

Install Docker Engine with Compose, or Docker Desktop on Windows/macOS. Your host must remain awake and connected for checks to run. From a current checkout:

```sh
cp deploy/worker/.env.example deploy/worker/.env
```

Edit `.env` locally with the existing runtime database DSN, engine encryption key, OpenAI key, and model. Do not generate a different encryption key: it would prevent decryption of saved broker connections. Use `desk_runtime` credentials, not the database administrator. Do not copy a retired scanner `.env`. The file is ignored by git. On Linux restrict it to your user with `chmod 600 deploy/worker/.env`.

```sh
docker compose -f deploy/worker/compose.yaml up -d --build
docker compose -f deploy/worker/compose.yaml logs --tail=50 -f
```

The default command runs **shadow decisions only**, even if a stored schedule/profile was previously armed. It records decisions without previewing or submitting broker orders. It does not create schedules, arm profiles, change risk settings, or disable kill switches. Worker heartbeat is visible in the dashboard's automation health. With no enabled schedules it simply waits.

## Explicit demo activation

First configure the profile's instrument universe, risk per trade, daily loss, exposure and position limits, sessions, providers, and schedule. Verify AI dry runs and the complete manual demo lifecycle. Required news/macro providers still fail closed; the official research-feed fallback is not a complete news-blackout feed. Broker-side stops remain active while the worker is offline, but the worker cannot perform new checks then.

After explicitly deciding to enable unattended **demo** execution, change the Compose command to:

```yaml
command: ["--execute-demo"]
```

Then recreate the service with `docker compose -f deploy/worker/compose.yaml up -d`. The database's kill switch, demo enablement, profile enablement, risk policies, and schedule remain authoritative. Starting the container never toggles them. Live autonomous execution remains unsupported.

To stop new autonomous submissions, enable the kill switch in the dashboard. To stop the process:

```sh
docker compose -f deploy/worker/compose.yaml down
```

Stopping or killing the worker does not close existing positions or cancel broker orders. Verify and manage them through Trade or the broker. Keep a single worker initially; scheduler leases and durable submission keys protect against duplicate dispatch, but an uncertain broker result must be reconciled rather than retried as a new order.

## Deployment status

This setup supplies the scheduler process, not a new position-management strategy. It has not been installed on the owner's host. Continuous stop adjustment/trailing management requires its own tested policy and worker integration; do not assume that periodic entry decisions manage existing positions.
