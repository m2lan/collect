from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ROLE_LABEL, CurrentUser, SessionDep
from app.audit import write_audit
from app.models import User
from app.models.entities import utcnow
from app.schemas.api import LoginIn, TokenOut
from app.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, session: SessionDep) -> TokenOut:
    user = await session.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash) or not user.is_active:
        await write_audit(body.username, None, "登录失败", request, 401)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误，或账号已停用")
    user.last_login_at = utcnow()
    await session.commit()
    await write_audit(user.username, user.id, "登录", request, 200)
    return TokenOut(access_token=create_token(user.username))


@router.get("/me")
async def me(user: CurrentUser) -> dict:
    return {
        "id": user.id, "username": user.username, "display_name": user.display_name or user.username,
        "role": user.role, "role_label": ROLE_LABEL.get(user.role, user.role), "is_superuser": user.role == "admin",
    }


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


@router.put("/password")
async def change_password(body: PasswordIn, user: CurrentUser, session: SessionDep) -> dict:
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "原密码不正确")
    db_user = await session.get(User, user.id)
    assert db_user
    db_user.password_hash = hash_password(body.new_password)
    await session.commit()
    return {"ok": True}
