from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.models import Notification, Subscription
from app.notify import send_webhook
from app.schemas.api import NotificationOut, Page, SubscriptionIn, SubscriptionOut
from app.settings_store import get_setting

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def _clean(words: list[str]) -> list[str]:
    return [w.strip() for w in words if w and w.strip()]


def _apply(sub: Subscription, body: SubscriptionIn) -> None:
    data = body.model_dump()
    for k in ("keywords", "exclude_keywords", "tags"):
        data[k] = _clean(data[k])
    if "price_reach" in body.events and body.target_price is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "“价格达到目标”需要填写目标价")
    data["webhook_url"] = (data["webhook_url"] or "").strip() or None
    if not data["webhook_url"]:
        data["webhook_type"] = None
    for k, v in data.items():
        setattr(sub, k, v)


async def _out(session: SessionDep, sub: Subscription) -> SubscriptionOut:
    n = await session.scalar(select(func.count()).select_from(Notification).where(Notification.subscription_id == sub.id))
    return SubscriptionOut.model_validate(sub).model_copy(update={"notified": n or 0})


@router.get("", response_model=list[SubscriptionOut])
async def list_subscriptions(session: SessionDep) -> list[SubscriptionOut]:
    subs = (await session.scalars(select(Subscription).order_by(Subscription.id.desc()))).all()
    return [await _out(session, s) for s in subs]


@router.post("", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
async def create_subscription(body: SubscriptionIn, session: SessionDep) -> SubscriptionOut:
    sub = Subscription()
    _apply(sub, body)
    session.add(sub)
    await session.commit()
    return await _out(session, sub)


async def _get(session: SessionDep, sub_id: int) -> Subscription:
    sub = await session.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订阅不存在")
    return sub


@router.put("/{sub_id}", response_model=SubscriptionOut)
async def update_subscription(sub_id: int, body: SubscriptionIn, session: SessionDep) -> SubscriptionOut:
    sub = await _get(session, sub_id)
    _apply(sub, body)
    await session.commit()
    return await _out(session, sub)


@router.delete("/{sub_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(sub_id: int, session: SessionDep) -> None:
    await session.delete(await _get(session, sub_id))
    await session.commit()


@router.post("/{sub_id}/test")
async def test_subscription(sub_id: int, session: SessionDep) -> dict:
    sub = await _get(session, sub_id)
    kind = sub.webhook_type or await get_setting(session, "alert.webhook_type")
    url = sub.webhook_url or await get_setting(session, "alert.webhook_url")
    ok, detail = await send_webhook(kind, url, f"订阅「{sub.name}」测试消息", "订阅推送通道工作正常")
    return {"ok": ok, "message": detail}


@router.get("/notifications", response_model=Page[NotificationOut])
async def list_notifications(
    session: SessionDep, subscription_id: int | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
) -> Page[NotificationOut]:
    stmt = select(Notification, Subscription.name).join(Subscription, Subscription.id == Notification.subscription_id)
    count = select(func.count()).select_from(Notification)
    if subscription_id:
        stmt = stmt.where(Notification.subscription_id == subscription_id)
        count = count.where(Notification.subscription_id == subscription_id)
    rows = (await session.execute(stmt.order_by(Notification.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    items = [NotificationOut.model_validate(n).model_copy(update={"subscription_name": name}) for n, name in rows]
    return Page[NotificationOut](total=await session.scalar(count) or 0, items=items)
