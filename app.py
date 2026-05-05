import os

from class_coordinator.web import run


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "41234"))
    run(host=host, port=port)


if __name__ == "__main__":
    main()
