"""A real Uvicorn listener with test-only outbound allowlisting.

Only this listener's numeric loopback address/port is reachable. This is not an
OS network sandbox and must never be used with untrusted code or real secrets.
"""

import asyncio
import socket
from contextlib import asynccontextmanager

import httpx
import uvicorn


@asynccontextmanager
async def loopback_http_client(app, monkeypatch, original_connect, original_getaddrinfo):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    listener.setblocking(False)
    address = listener.getsockname()

    def connect(sock, target):
        if target != address:
            raise AssertionError("NON-TEST NETWORK FORBIDDEN")
        return original_connect(sock, target)

    def getaddrinfo(host, port, *args, **kwargs):
        if (host, port) != address:
            raise AssertionError("NON-TEST DNS FORBIDDEN")
        return original_getaddrinfo(host, port, *args, **kwargs)

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=address[0],
            port=address[1],
            lifespan="off",
            access_log=False,
            log_level="error",
            loop="asyncio",
            timeout_graceful_shutdown=2,
        )
    )
    task = None
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(socket.socket, "connect", connect)
            scoped.setattr(socket, "getaddrinfo", getaddrinfo)
            task = asyncio.create_task(server.serve(sockets=[listener]))
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise AssertionError("test HTTP server did not start")
                    await asyncio.sleep(0.01)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{address[1]}",
                trust_env=False,
            ) as client:
                yield client
    finally:
        server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, 5)
            finally:
                if not task.done():
                    task.cancel()
        listener.close()
