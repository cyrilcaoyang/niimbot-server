import argparse
import json
from pathlib import Path

from .api import create_app
from .models import Settings


def main():
    parser = argparse.ArgumentParser(
        description="Niimbot D110_M / B1 device service (simulation by default)"
    )
    parser.add_argument("--config", type=Path, help="Machine-local JSON configuration")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    settings = (
        Settings.model_validate(json.loads(args.config.read_text())) if args.config else Settings()
    )
    import uvicorn

    uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
