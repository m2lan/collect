from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import SessionDep, current_user
from app.models import User
from app.schemas.api import LoginIn, TokenOut
from app.security import create_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, session: SessionDep) -> TokenOut:
    user = await session.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    return TokenOut(access_token=create_token(user.username))


@router.get("/me")
async def me(user: Annotated[User, Depends(current_user)]) -> dict:
    return {"id": user.id, "username": user.username, "is_superuser": user.is_superuser}
