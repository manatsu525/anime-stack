"""Application configuration (env-overridable)."""
import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
LOG_DIR = BASE_DIR / "logs"
SESSION_DIR = BASE_DIR / "session"
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/home/share"))

# Web server
HOST = os.environ.get("WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("WEB_PORT", "8765"))

# aria2 JSON-RPC
ARIA2_RPC_HOST = os.environ.get("ARIA2_RPC_HOST", "127.0.0.1")
ARIA2_RPC_PORT = int(os.environ.get("ARIA2_RPC_PORT", "6800"))
ARIA2_RPC_SECRET = os.environ.get("ARIA2_RPC_SECRET", "animehub")
ARIA2_RPC_URL = f"http://{ARIA2_RPC_HOST}:{ARIA2_RPC_PORT}/jsonrpc"

# HTTP client
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "20"))

# Search sources
SOURCES = {
    "dmhy": {
        "name": "动漫花园",
        "color": "#e85d04",
        "search_rss": "https://share.dmhy.org/topics/rss/rss.xml",
        "latest_rss": "https://share.dmhy.org/topics/rss/rss.xml",
        "site": "https://share.dmhy.org",
    },
    "acgrip": {
        "name": "ACG.RIP",
        "color": "#2a9d8f",
        "search_rss": "https://acg.rip/.xml",
        "latest_rss": "https://acg.rip/.xml",
        "site": "https://acg.rip",
    },
    "nyaa": {
        "name": "Nyaa",
        "color": "#3a5a40",
        "search_rss": "https://nyaa.si/",
        "latest_rss": "https://nyaa.si/?page=rss&c=1_0&f=0",
        "site": "https://nyaa.si",
    },
}
