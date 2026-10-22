import json
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.config import get_settings

router = APIRouter(prefix="/backups", tags=["backups"])
NAME = re.compile(r"^collect_\d{8}_\d{6}\.sql\.gz$")


def _dir() -> Path:
    return Path(get_settings().backup_dir)


@router.get("")
async def list_backups() -> dict:
    d = _dir()
    if not d.is_dir():
        return {"enabled": False, "files": [], "status": None, "pending": False}
    files = sorted((p for p in d.glob("collect_*.sql.gz") if NAME.match(p.name)), reverse=True)
    status_file = d / ".status.json"
    try:
        last = json.loads(status_file.read_text()) if status_file.exists() else None
    except (OSError, json.JSONDecodeError):
        last = None
    return {
        "enabled": True,
        "pending": (d / ".request").exists(),
        "status": last,
        "files": [
            {"name": p.name, "size": p.stat().st_size, "created_at": datetime.fromtimestamp(p.stat().st_mtime, UTC)}
            for p in files
        ],
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def request_backup() -> dict:
    d = _dir()
    if not d.is_dir():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "未启用备份服务（BACKUP_DIR 不存在）")
    (d / ".request").write_text(datetime.now(UTC).isoformat())
    return {"ok": True, "message": "已提交备份请求，备份服务将在 15 秒内开始执行"}


@router.get("/{name}")
async def download(name: str) -> FileResponse:
    path = _dir() / name
    if not NAME.match(name) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "备份文件不存在")
    return FileResponse(path, media_type="application/gzip", filename=name)
