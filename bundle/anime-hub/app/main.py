"""Anime Hub — aggregate anime torrent search + aria2 download manager."""
from __future__ import annotations

from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .aria2 import (
    Aria2Error,
    TASK_KEYS,
    aria2,
    detect_uri_kind,
    normalize_file,
    normalize_task,
)
from .config import DOWNLOAD_DIR, PORT, SOURCES, STATIC_DIR
from .files import delete_path, delete_task_files, list_dir, resolve_under_root
from .sources import search_all, search_source

app = FastAPI(title="Anime Hub", version="1.0.0", docs_url="/api/docs")


class SearchResponse(BaseModel):
    keyword: str
    items: list
    total: int
    sources: list
    errors: dict


class AddDownloadBody(BaseModel):
    magnet: Optional[str] = Field(None, description="magnet:?xt=...")
    torrent_url: Optional[str] = Field(None, description="http(s) torrent URL")
    uri: Optional[str] = Field(
        None, description="generic URI: magnet / .torrent URL / http(s) file URL"
    )
    name: Optional[str] = Field(None, description="output filename (HTTP) or hint")
    pause: bool = Field(False, description="add task in paused state")
    pause_metadata: bool = Field(
        False,
        description="for magnet/torrent: pause after metadata so user can pick files",
    )
    select_file: Optional[str] = Field(
        None, description='comma-separated 1-based file indexes, e.g. "1,3,5"'
    )
    referer: Optional[str] = Field(None, description="HTTP Referer header")
    user_agent: Optional[str] = Field(None, description="override User-Agent")


class SelectFilesBody(BaseModel):
    indexes: List[int] = Field(
        ...,
        description="1-based aria2 file indexes to download (others skipped)",
        min_length=1,
    )


class GidBody(BaseModel):
    gid: str


class DeleteFileBody(BaseModel):
    path: str = Field(..., description="relative path under download dir")


@app.get("/api/health")
async def health():
    ok = await aria2.ping()
    return {
        "ok": True,
        "aria2": ok,
        "download_dir": str(DOWNLOAD_DIR),
        "port": PORT,
    }


@app.get("/api/sources")
async def list_sources():
    return {
        "sources": [
            {"id": k, "name": v["name"], "color": v["color"], "site": v["site"]}
            for k, v in SOURCES.items()
        ]
    }


@app.get("/api/search")
async def api_search(
    q: str = Query("", description="search keyword; empty = latest"),
    source: Optional[str] = Query(None, description="comma-separated: dmhy,acgrip,nyaa"),
):
    sources = None
    if source:
        sources = [s.strip() for s in source.split(",") if s.strip()]
        bad = [s for s in sources if s not in SOURCES]
        if bad:
            raise HTTPException(400, f"unknown source(s): {bad}")
    result = await search_all(q, sources)
    return result


@app.get("/api/search/{source_id}")
async def api_search_one(source_id: str, q: str = Query("")):
    if source_id not in SOURCES:
        raise HTTPException(404, f"unknown source: {source_id}")
    return await search_source(source_id, q)


@app.get("/api/downloads")
async def list_downloads():
    try:
        active = await aria2.tell_active(TASK_KEYS)
        waiting = await aria2.tell_waiting(0, 1000, TASK_KEYS)
        stopped = await aria2.tell_stopped(0, 1000, TASK_KEYS)
        stat = await aria2.get_global_stat()
    except Exception as e:
        raise HTTPException(503, f"aria2 unavailable: {e}") from e

    tasks = [normalize_task(t) for t in (active + waiting + stopped)]
    # de-dupe by gid (prefer active); hide pure "removed" shell entries
    seen = set()
    unique = []
    for t in tasks:
        if t["gid"] in seen:
            continue
        if t.get("status") == "removed":
            continue
        seen.add(t["gid"])
        unique.append(t)

    return {
        "tasks": unique,
        "stat": {
            "download_speed": int(stat.get("downloadSpeed") or 0),
            "upload_speed": int(stat.get("uploadSpeed") or 0),
            "num_active": int(stat.get("numActive") or 0),
            "num_waiting": int(stat.get("numWaiting") or 0),
            "num_stopped": int(stat.get("numStopped") or 0),
        },
    }


@app.post("/api/downloads")
async def add_download(body: AddDownloadBody):
    uri = (body.uri or body.magnet or body.torrent_url or "").strip()
    if not uri:
        raise HTTPException(400, "magnet / torrent_url / uri is required")

    # basic validation
    lower = uri.lower()
    if not (
        lower.startswith("magnet:")
        or lower.startswith("http://")
        or lower.startswith("https://")
        or lower.startswith("ftp://")
    ):
        raise HTTPException(
            400, "unsupported URI scheme (use magnet: / http(s): / ftp:)"
        )

    kind = detect_uri_kind(uri)
    opts: dict = {}
    if body.name:
        opts["out"] = body.name
    if body.pause:
        opts["pause"] = "true"
    if body.pause_metadata and kind in ("magnet", "torrent"):
        # After metadata is ready the task pauses — pick files then resume
        opts["pause-metadata"] = "true"
    if body.select_file:
        opts["select-file"] = body.select_file
    if body.referer:
        opts["referer"] = body.referer
    if body.user_agent:
        opts["user-agent"] = body.user_agent
    # Pure HTTP/FTP: never treat response as torrent unless URL is .torrent
    if kind == "http":
        opts.setdefault("follow-torrent", "false")

    try:
        gid = await aria2.add_uri(uri, opts or None)
    except Aria2Error as e:
        raise HTTPException(400, f"aria2 error: {e}") from e
    except Exception as e:
        raise HTTPException(503, f"aria2 unavailable: {e}") from e

    return {"ok": True, "gid": gid, "uri": uri[:200], "kind": kind}


@app.get("/api/downloads/{gid}/files")
async def list_download_files(gid: str):
    """List files inside a task (for multi-file torrents)."""
    try:
        files = await aria2.get_files(gid)
        status = await aria2.tell_status(gid, ["gid", "status", "bittorrent", "infoHash"])
    except Aria2Error as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:
        raise HTTPException(503, f"aria2 unavailable: {e}") from e
    return {
        "gid": gid,
        "status": status.get("status"),
        "files": [normalize_file(f) for f in (files or [])],
    }


@app.post("/api/downloads/{gid}/files")
async def select_download_files(gid: str, body: SelectFilesBody):
    """Select which torrent files to download (1-based indexes)."""
    try:
        await aria2.select_files(gid, body.indexes)
        files = await aria2.get_files(gid)
        return {
            "ok": True,
            "gid": gid,
            "indexes": sorted(set(int(i) for i in body.indexes if int(i) > 0)),
            "files": [normalize_file(f) for f in (files or [])],
        }
    except Aria2Error as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(503, f"aria2 unavailable: {e}") from e


@app.post("/api/downloads/{gid}/pause")
async def pause_download(gid: str):
    try:
        await aria2.pause(gid)
        return {"ok": True, "gid": gid, "action": "pause"}
    except Aria2Error as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/downloads/{gid}/resume")
async def resume_download(gid: str):
    try:
        await aria2.unpause(gid)
        return {"ok": True, "gid": gid, "action": "resume"}
    except Aria2Error as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/downloads/{gid}")
async def remove_download(
    gid: str,
    delete_result: bool = Query(True),
    delete_files: bool = Query(False, description="also delete local download files"),
):
    """Remove a task. Optionally delete on-disk files (completed or partial)."""
    file_result = None
    try:
        status = None
        st = None
        try:
            status = await aria2.tell_status(gid, TASK_KEYS)
            st = status.get("status")
        except Aria2Error:
            st = None

        # Capture paths before removing from aria2
        if delete_files and status:
            try:
                file_result = delete_task_files(status)
            except Exception as e:
                file_result = {"deleted": [], "errors": [str(e)], "count": 0}

        if st in ("active", "waiting", "paused"):
            try:
                await aria2.remove(gid)
            except Aria2Error:
                await aria2.call("aria2.forceRemove", [gid])
        # Always try to clear from result list
        if delete_result:
            try:
                await aria2.remove_result(gid)
            except Aria2Error:
                pass

        # If we couldn't read status earlier, nothing to delete on disk was done
        return {
            "ok": True,
            "gid": gid,
            "action": "remove",
            "delete_files": delete_files,
            "files": file_result,
        }
    except Aria2Error as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(503, str(e)) from e


@app.post("/api/downloads/purge")
async def purge_completed():
    try:
        await aria2.purge_download_result()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(503, str(e)) from e


@app.get("/api/downloads/{gid}")
async def get_download(gid: str):
    try:
        task = await aria2.tell_status(gid, TASK_KEYS)
        return normalize_task(task)
    except Aria2Error as e:
        raise HTTPException(404, str(e)) from e


# ---------- Download directory file browser ----------


@app.get("/api/files")
async def api_list_files(path: str = Query("", description="relative path under download dir")):
    try:
        return list_dir(path)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except NotADirectoryError as e:
        raise HTTPException(400, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/files")
async def api_delete_file(path: str = Query(..., description="relative path under download dir")):
    try:
        return delete_path(path)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except OSError as e:
        raise HTTPException(500, f"delete failed: {e}") from e


@app.post("/api/files/delete")
async def api_delete_file_post(body: DeleteFileBody):
    """POST variant for clients that prefer JSON body."""
    return await api_delete_file(path=body.path)


@app.get("/api/files/download")
async def api_download_file(path: str = Query(..., description="relative path under download dir")):
    """Download a file, or zip a directory, to the browser."""
    import io
    import zipfile
    from urllib.parse import quote

    try:
        target = resolve_under_root(path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    if not target.exists():
        raise HTTPException(404, f"not found: {path}")

    # Block downloading credential files
    name_lower = target.name.lower()
    if "credential" in name_lower or name_lower.endswith(".env"):
        raise HTTPException(403, "refusing to download credential/secret files")

    if target.is_file():
        # Force download with original filename (UTF-8 friendly)
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(target.name)}"
        }
        return FileResponse(
            path=str(target),
            filename=target.name,
            media_type="application/octet-stream",
            headers=headers,
        )

    if target.is_dir():
        # Stream a zip of the folder (skip protected install dirs if zipping root-ish)
        buf = io.BytesIO()
        base = target
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in base.rglob("*"):
                if not fp.is_file():
                    continue
                # skip credentials inside tree
                if "credential" in fp.name.lower():
                    continue
                arc = str(fp.relative_to(base.parent))
                try:
                    zf.write(fp, arcname=arc)
                except OSError:
                    continue
        buf.seek(0)
        zip_name = f"{target.name}.zip"
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_name)}"
        }
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers=headers,
        )

    raise HTTPException(400, "unsupported path type")


@app.get("/")
async def index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(404, "frontend not found")
    return FileResponse(index_file)
