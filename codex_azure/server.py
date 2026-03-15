import uvicorn

from .app import PROXY_HOST, PROXY_PORT, app


def main() -> None:
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT)


if __name__ == "__main__":
    main()