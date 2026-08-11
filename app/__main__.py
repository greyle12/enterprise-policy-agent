from __future__ import annotations

import uvicorn

from app.core.config import get_settings


def main() -> None:
    """Run the FastAPI application from validated environment settings."""

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        proxy_headers=True,
        server_header=False,
    )


if __name__ == "__main__":
    main()
