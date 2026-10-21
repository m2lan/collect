from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.security import decode_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]
_bearer = HTTPBearer(auto_error=False)


async def current_user(
    session: SessionDep, cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> User:
    username = decode_token(cred.credentials) if cred else None
    user = await session.scalar(select(User).where(User.username == username)) if username else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录或登录已过期")
    return user


async def superuser(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要超级管理员权限")
    return user
