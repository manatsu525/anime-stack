"""Minimal aria2 JSON-RPC client (BT + HTTP downloads)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import unquote, urlparse

import httpx

from .config import ARIA2_RPC_SECRET, ARIA2_RPC_URL, DOWNLOAD_DIR, HTTP_TIMEOUT, USER_AGENT


class Aria2Error(Exception):
    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


def detect_uri_kind(uri: str) -> str:
    """Return 'magnet' | 'torrent' | 'http' for a download URI."""
    u = (uri or "").strip()
    lower = u.lower()
    if lower.startswith("magnet:"):
        return "magnet"
    if lower.startswith("http://") or lower.startswith("https://"):
        path = urlparse(u).path.lower()
        if path.endswith(".torrent") or ".torrent?" in lower:
            return "torrent"
        return "http"
    return "http"


class Aria2Client:
    def __init__(
        self,
        rpc_url: str = ARIA2_RPC_URL,
        secret: str = ARIA2_RPC_SECRET,
    ):
        self.rpc_url = rpc_url
        self.secret = secret
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def call(self, method: str, params: Optional[List[Any]] = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": [f"token:{self.secret}"] + (params or []),
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(self.rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            err = data["error"]
            raise Aria2Error(err.get("message", str(err)), err.get("code"))
        return data.get("result")

    async def ping(self) -> bool:
        try:
            ver = await self.call("aria2.getVersion")
            return bool(ver)
        except Exception:
            return False

    async def get_version(self) -> Dict[str, Any]:
        return await self.call("aria2.getVersion")

    # Extra public/ACG trackers: seed magnet/torrent files often only list dead trackers
    DEFAULT_BT_TRACKERS = (
        "http://nyaa.tracker.wf:7777/announce,"
        "http://tracker.mywaifu.best:6969/announce,"
        "http://t.nyaatracker.com/announce,"
        "udp://tracker.torrent.eu.org:451/announce,"
        "udp://open.stealth.si:80/announce,"
        "udp://exodus.desync.com:6969/announce,"
        "udp://tracker.opentrackr.org:1337/announce,"
        "udp://tracker.moeking.me:6969/announce,"
        "http://tracker.openbittorrent.com:80/announce,"
        "udp://opentor.net:6969,"
        "http://open.acgtracker.com:1096/announce"
    )

    def _bt_options(self) -> Dict[str, str]:
        return {
            "dir": str(DOWNLOAD_DIR),
            "continue": "true",
            "max-connection-per-server": "16",
            "split": "16",
            "min-split-size": "1M",
            "bt-enable-lpd": "true",
            "bt-save-metadata": "true",
            "bt-load-saved-metadata": "true",
            "seed-ratio": "0.1",
            "bt-max-peers": "128",
            "bt-tracker": self.DEFAULT_BT_TRACKERS,
            "bt-tracker-connect-timeout": "10",
            "bt-tracker-timeout": "10",
            "follow-torrent": "true",
            "check-integrity": "false",
            "file-allocation": "none",
            "bt-remove-unselected-file": "false",
        }

    def _http_options(self) -> Dict[str, str]:
        return {
            "dir": str(DOWNLOAD_DIR),
            "continue": "true",
            "max-connection-per-server": "16",
            "split": "16",
            "min-split-size": "1M",
            "file-allocation": "none",
            "check-integrity": "false",
            "always-resume": "true",
            "auto-file-renaming": "true",
            "allow-overwrite": "false",
            "user-agent": USER_AGENT,
            # Do not treat arbitrary HTTP bodies as torrents unless URL is .torrent
            "follow-torrent": "false",
            "follow-metalink": "false",
        }

    def options_for_uri(self, uri: str) -> Dict[str, str]:
        kind = detect_uri_kind(uri)
        if kind in ("magnet", "torrent"):
            opts = self._bt_options()
            if kind == "torrent":
                opts["follow-torrent"] = "true"
            return opts
        return self._http_options()

    async def add_uri(
        self,
        uri: str,
        options: Optional[Dict[str, str]] = None,
    ) -> str:
        opts = self.options_for_uri(uri)
        if options:
            opts.update({k: str(v) for k, v in options.items() if v is not None})
        return await self.call("aria2.addUri", [[uri], opts])

    async def add_torrent_url(self, torrent_url: str, options: Optional[Dict[str, str]] = None) -> str:
        """Add by torrent HTTP URL (aria2 will fetch then download)."""
        return await self.add_uri(torrent_url, options)

    async def tell_active(self, keys: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return await self.call("aria2.tellActive", [keys] if keys else [])

    async def tell_waiting(
        self, offset: int = 0, num: int = 1000, keys: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [offset, num]
        if keys:
            params.append(keys)
        return await self.call("aria2.tellWaiting", params)

    async def tell_stopped(
        self, offset: int = 0, num: int = 1000, keys: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [offset, num]
        if keys:
            params.append(keys)
        return await self.call("aria2.tellStopped", params)

    async def tell_status(self, gid: str, keys: Optional[List[str]] = None) -> Dict[str, Any]:
        params: List[Any] = [gid]
        if keys:
            params.append(keys)
        return await self.call("aria2.tellStatus", params)

    async def get_files(self, gid: str) -> List[Dict[str, Any]]:
        return await self.call("aria2.getFiles", [gid])

    async def change_option(self, gid: str, options: Dict[str, str]) -> str:
        return await self.call("aria2.changeOption", [gid, options])

    async def select_files(self, gid: str, indexes: Sequence[int]) -> str:
        """Select which torrent files to download (1-based indexes)."""
        idxs = sorted({int(i) for i in indexes if int(i) > 0})
        if not idxs:
            raise Aria2Error("at least one file index is required")
        # aria2 select-file is a comma-separated list of indexes
        return await self.change_option(gid, {"select-file": ",".join(str(i) for i in idxs)})

    async def pause(self, gid: str) -> str:
        return await self.call("aria2.pause", [gid])

    async def unpause(self, gid: str) -> str:
        return await self.call("aria2.unpause", [gid])

    async def remove(self, gid: str) -> str:
        """Remove active/waiting download (keeps files)."""
        try:
            return await self.call("aria2.remove", [gid])
        except Aria2Error:
            return await self.call("aria2.forceRemove", [gid])

    async def remove_result(self, gid: str) -> str:
        """Remove completed/error result from list."""
        return await self.call("aria2.removeDownloadResult", [gid])

    async def pause_all(self) -> str:
        return await self.call("aria2.pauseAll")

    async def unpause_all(self) -> str:
        return await self.call("aria2.unpauseAll")

    async def purge_download_result(self) -> str:
        return await self.call("aria2.purgeDownloadResult")

    async def get_global_stat(self) -> Dict[str, Any]:
        return await self.call("aria2.getGlobalStat")


TASK_KEYS = [
    "gid",
    "status",
    "totalLength",
    "completedLength",
    "uploadLength",
    "downloadSpeed",
    "uploadSpeed",
    "connections",
    "numSeeders",
    "seeder",
    "errorCode",
    "errorMessage",
    "dir",
    "files",
    "bittorrent",
    "infoHash",
    "followedBy",
    "following",
    "belongsTo",
    "verifiedLength",
    "verifyIntegrityPending",
]


def _file_basename(path: str) -> str:
    if not path:
        return ""
    path = path.rstrip("/")
    return path.rsplit("/", 1)[-1] or path


def _task_name(task: Dict[str, Any]) -> str:
    bt = task.get("bittorrent") or {}
    info = bt.get("info") or {}
    if info.get("name"):
        return info["name"]
    files = task.get("files") or []
    if files:
        path = files[0].get("path") or ""
        if path:
            return _file_basename(path) or path
        uris = files[0].get("uris") or []
        if uris:
            uri = uris[0].get("uri", "")
            # Prefer last path segment of URL
            try:
                p = unquote(urlparse(uri).path)
                base = _file_basename(p)
                if base:
                    return base
            except Exception:
                pass
            return uri[:160]
    return task.get("gid", "unknown")


def _detect_task_kind(task: Dict[str, Any]) -> str:
    """bt | http | metadata"""
    bt = task.get("bittorrent") or {}
    name = _task_name(task)
    if name.startswith("[METADATA]") or (bt and not (bt.get("info") or {}).get("name") and task.get("infoHash")):
        # metadata-only magnet phase often still has bittorrent mode without full info
        files = task.get("files") or []
        if files and len(files) == 1:
            p = (files[0].get("path") or "").lower()
            if "metadata" in p or not p:
                if task.get("infoHash") and not (bt.get("info") or {}).get("name"):
                    return "metadata"
    if bt or task.get("infoHash"):
        return "bt"
    return "http"


def normalize_file(f: Dict[str, Any]) -> Dict[str, Any]:
    total = int(f.get("length") or 0)
    done = int(f.get("completedLength") or 0)
    progress = (done / total * 100.0) if total > 0 else 0.0
    path = f.get("path") or ""
    selected_raw = f.get("selected")
    if isinstance(selected_raw, bool):
        selected = selected_raw
    else:
        selected = str(selected_raw).lower() in ("true", "1", "yes")
    uris = []
    for u in f.get("uris") or []:
        if isinstance(u, dict):
            uris.append({"uri": u.get("uri"), "status": u.get("status")})
        elif u:
            uris.append({"uri": str(u), "status": None})
    return {
        "index": int(f.get("index") or 0),
        "path": path,
        "name": _file_basename(path) or path or f"file#{f.get('index')}",
        "length": total,
        "completed_length": done,
        "progress": round(progress, 2),
        "selected": selected,
        "uris": uris,
    }


def normalize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    total = int(task.get("totalLength") or 0)
    done = int(task.get("completedLength") or 0)
    dlspeed = int(task.get("downloadSpeed") or 0)
    ulspeed = int(task.get("uploadSpeed") or 0)
    progress = (done / total * 100.0) if total > 0 else 0.0
    files = [normalize_file(f) for f in (task.get("files") or [])]
    kind = _detect_task_kind(task)
    # refine metadata detection
    name = _task_name(task)
    if name.startswith("[METADATA]"):
        kind = "metadata"
    selected_count = sum(1 for f in files if f["selected"])
    return {
        "gid": task.get("gid"),
        "name": name,
        "status": task.get("status"),
        "kind": kind,  # bt | http | metadata
        "total_length": total,
        "completed_length": done,
        "progress": round(progress, 2),
        "download_speed": dlspeed,
        "upload_speed": ulspeed,
        "connections": int(task.get("connections") or 0),
        "num_seeders": int(task.get("numSeeders") or 0) if task.get("numSeeders") is not None else None,
        "info_hash": task.get("infoHash"),
        "dir": task.get("dir"),
        "error_code": task.get("errorCode"),
        "error_message": task.get("errorMessage"),
        "followed_by": task.get("followedBy") or [],
        "following": task.get("following"),
        "belongs_to": task.get("belongsTo"),
        "num_files": len(files),
        "selected_files": selected_count,
        "files": files,
    }


aria2 = Aria2Client()
