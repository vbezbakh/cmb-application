"""Start the HTTP server: ``python -m app``.

One uvicorn worker per container. Kubernetes scales pods, not processes inside a pod:
CPU requests stay honest (one Python process uses at most ~1 core), probes and restarts
apply to exactly one process, and metrics need no multiprocess workarounds.
"""

import uvicorn

from app.config import Settings
from app.logs import configure_logging


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.component)
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        server_header=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_keep_alive=settings.keep_alive_seconds,
        timeout_graceful_shutdown=settings.graceful_shutdown_seconds,
    )


if __name__ == "__main__":
    main()
