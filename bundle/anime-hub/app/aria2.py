"""Minimal aria2 JSON-RPC client."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import httpx

from .config import ARIA2_RPC_SECRET, ARIA2_RPC_URL, DOWNLOAD_DIR, HTTP_TIMEOUT


class Aria2Error(Exception):
    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


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
    # (e.g. open.acgtracker.com). Injecting these lets aria2 find peers much faster.
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

    async def add_uri(
        self,
        uri: str,
        options: Optional[Dict[str, str]] = None,
    ) -> str:
        opts = {
            "dir": str(DOWNLOAD_DIR),
            "continue": "true",
            "max-connection-per-server": "16",
            "split": "16",
            "min-split-size": "1M",
            "bt-enable-lpd": "true",
            "bt-save-metadata": "true",
            "bt-load-saved-metadata": "true",
            # Seed until share ratio reaches 10%, then stop (saves upload bandwidth)
            "seed-ratio": "0.1",
            "bt-max-peers": "128",
            "bt-tracker": self.DEFAULT_BT_TRACKERS,
            "bt-tracker-connect-timeout": "10",
            "bt-tracker-timeout": "10",
            "follow-torrent": "true",
            "check-integrity": "false",
            "file-allocation": "none",
        }
        if options:
            opts.update(options)
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


def _task_name(task: Dict[str, Any]) -> str:
    bt = task.get("bittorrent") or {}
    info = bt.get("info") or {}
    if info.get("name"):
        return info["name"]
    files = task.get("files") or []
    if files:
        path = files[0].get("path") or ""
        if path:
            return path.rsplit("/", 1)[-1] or path
        uris = files[0].get("uris") or []
        if uris:
            return uris[0].get("uri", "")[:120]
    return task.get("gid", "unknown")


def normalize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    total = int(task.get("totalLength") or 0)
    done = int(task.get("completedLength") or 0)
    dlspeed = int(task.get("downloadSpeed") or 0)
    ulspeed = int(task.get("uploadSpeed") or 0)
    progress = (done / total * 100.0) if total > 0 else 0.0
    return {
        "gid": task.get("gid"),
        "name": _task_name(task),
        "status": task.get("status"),
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
        "files": [
            {
                "path": f.get("path"),
                "length": int(f.get("length") or 0),
                "completed_length": int(f.get("completedLength") or 0),
            }
            for f in (task.get("files") or [])
        ],
    }


aria2 = Aria2Client()
