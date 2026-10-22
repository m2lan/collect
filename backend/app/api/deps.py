from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.security import decode_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]
_bearer = HTTPBearer(auto_error=False)

ROLES = {"viewer": 1, "operator": 2, "admin": 3}
ROLE_LABEL = {"viewer": "只读", "operator": "操作员", "admin": "管理员"}
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def current_user(
    session: SessionDep, cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> User:
    username = decode_token(cred.credentials) if cred else None
    user = await session.scalar(select(User).where(User.username == username)) if username else None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录、登录已过期或账号已停用")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def has_role(user: User, role: str) -> bool:
    return ROLES.get(user.role, 0) >= ROLES[role]


def require(read: str = "viewer", write: str = "operator") -> Callable:
    """路由级权限：读请求（GET）需要 read 角色，其余请求需要 write 角色。"""

    async def dep(request: Request, user: CurrentUser) -> User:
        need = read if request.method in READ_METHODS else write
        if not has_role(user, need):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"需要{ROLE_LABEL[need]}及以上权限")
        return user

    return dep


async def admin(user: CurrentUser) -> User:
    if not has_role(user, "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user


# 兼容旧名称
superuser = admin
