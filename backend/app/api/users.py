from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import AuditLog, User
from app.schemas.api import Page
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])
audit_router = APIRouter(prefix="/audit", tags=["audit"])

Role = Literal["admin", "operator", "viewer"]


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    display_name: str | None = Field(None, max_length=64)
    role: Role = "viewer"
    is_active: bool = True
    password: str | None = Field(None, min_length=6, max_length=128, description="新建时必填；编辑时留空不修改")


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str | None
    role: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


async def _admins_left(session: SessionDep, excluding: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(User.role == "admin", User.is_active, User.id != excluding)
    ) or 0


@router.get("", response_model=list[UserOut])
async def list_users(session: SessionDep) -> list[User]:
    return list((await session.scalars(select(User).order_by(User.id))).all())


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserIn, session: SessionDep) -> User:
    if not body.password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请设置初始密码（至少 6 位）")
    if await session.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已存在")
    user = User(username=body.username, display_name=body.display_name, role=body.role, is_active=body.is_active,
                is_superuser=body.role == "admin", password_hash=hash_password(body.password))
    session.add(user)
    await session.commit()
    return user


@router.put("/{user_id}", response_model=UserOut)
async def update_user(user_id: int, body: UserIn, me: CurrentUser, session: SessionDep) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    demoting = user.role == "admin" and (body.role != "admin" or not body.is_active)
    if demoting and not await _admins_left(session, user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "至少需要保留一个启用的管理员")
    if user.username != body.username and await session.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已存在")
    user.username, user.display_name, user.role, user.is_active = body.username, body.display_name, body.role, body.is_active
    user.is_superuser = body.role == "admin"
    if body.password:
        user.password_hash = hash_password(body.password)
    await session.commit()
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, me: CurrentUser, session: SessionDep) -> None:
    user = await session.get(User, user_id)
    if user is None:
        return
    if user.id == me.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能删除自己")
    if user.role == "admin" and not await _admins_left(session, user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "至少需要保留一个启用的管理员")
    await session.delete(user)
    await session.commit()


class AuditOut(BaseModel):
    id: int
    username: str | None
    action: str
    method: str
    path: str
    status_code: int
    ip: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


@audit_router.get("", response_model=Page[AuditOut])
async def list_audit(
    session: SessionDep, username: str | None = None, action: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
) -> Page[AuditOut]:
    stmt, count = select(AuditLog), select(func.count()).select_from(AuditLog)
    if username:
        stmt, count = stmt.where(AuditLog.username == username), count.where(AuditLog.username == username)
    if action:
        stmt, count = stmt.where(AuditLog.action.contains(action)), count.where(AuditLog.action.contains(action))
    rows = (await session.scalars(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return Page[AuditOut](total=await session.scalar(count) or 0, items=[AuditOut.model_validate(r) for r in rows])
