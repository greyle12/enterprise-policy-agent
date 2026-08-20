from __future__ import annotations

import uvicorn

from app.core.config import get_settings
from app.observability import build_json_logging_config


def main() -> None:
    """Run the FastAPI application from validated environment settings."""

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        proxy_headers=True,
        server_header=False,
        access_log=False,
        log_level=settings.log_level.lower(),
        log_config=build_json_logging_config(settings.log_level),
    )


if __name__ == "__main__":
    main()
