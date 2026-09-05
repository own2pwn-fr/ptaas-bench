"""Development entry point: `python -m hub`. The deployment runs gunicorn."""

from . import app
from .config import settings

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.listen_port, threaded=True)
