"""Self-hosted scheduler. Defaults to shadow decisions; never enables account controls."""
import argparse
import asyncio
import logging
import signal

from app.config.settings import settings
from app.jobs.autonomous_scheduler import AutonomousSchedulerWorker
from app.services.autonomous.runner import AutonomousDecisionRunner
from app.storage.database import check_database


class ShadowRunner(AutonomousDecisionRunner):
    async def run(self, user_sub, profile_ref, run_key, trigger_reason, *, allow_safe_retry=False):
        return await super().run(user_sub, profile_ref, run_key, trigger_reason,
                                 allow_safe_retry=allow_safe_retry, dry_run=True)


def preflight():
    if settings.storage_backend != "postgres" or settings.app_env != "production":
        raise RuntimeError("A self-hosted worker requires production Postgres storage.")
    if settings.live_trading_enabled or settings.autonomous_scheduler_embedded:
        raise RuntimeError("Disable live trading and the embedded scheduler for this worker.")
    if not settings.broker_secret_key and settings.broker_secret_keys_json == "{}":
        raise RuntimeError("Configure the same broker encryption key used by the dashboard engine.")
    check_database()


async def run(execute_demo=False):
    preflight()
    worker=AutonomousSchedulerWorker(runner_factory=AutonomousDecisionRunner if execute_demo else ShadowRunner)
    logging.getLogger(__name__).info("worker_started mode=%s", "demo_execution" if execute_demo else "shadow")
    loop=asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(event, worker.stop)
        except NotImplementedError: pass
    await worker.run_forever()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-demo", action="store_true", help="Allow configured demo profiles to execute. Does not arm profiles or disable kill switches.")
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(args.execute_demo))


if __name__ == "__main__": main()
