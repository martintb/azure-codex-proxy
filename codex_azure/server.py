import os

import uvicorn

from .app import PROXY_HOST, PROXY_PORT, app
from .config import clear_proxy_runtime_state, save_proxy_runtime_state


def main() -> None:
    config = uvicorn.Config(app, host=PROXY_HOST, port=PROXY_PORT)
    sock = config.bind_socket()
    bound_port = int(sock.getsockname()[1])
    save_proxy_runtime_state(pid=os.getpid(), host=PROXY_HOST, port=bound_port)
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    finally:
        clear_proxy_runtime_state()
        sock.close()


if __name__ == "__main__":
    main()
