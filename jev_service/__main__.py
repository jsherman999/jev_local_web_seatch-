import os

import uvicorn

from . import config  # noqa: F401

if __name__ == "__main__":
    os.umask(0o077)
    uvicorn.run(
        "jev_service.app:app",
        host=os.environ.get("JEV_HOST", "127.0.0.1"),
        port=int(os.environ.get("JEV_PORT", "8776")),
        workers=1,
        timeout_graceful_shutdown=10,
    )
