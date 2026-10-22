"""订阅提醒：采集后对新上架 / 价格变化的拍品匹配订阅条件并推送；调度器定时检查即将结束的拍品。"""

import logging
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lot, Notification, Source, Subscription
from app.models.entities import utcnow
from app.notify import send_webhook
from app.settings_store import get_setting

log = logging.getLogger(__name__)

EVENT_LABEL = {"new": "新上架", "price_change": "价格变化", "price_reach": "价格达到目标", "ending_soon": "即将结束"}
MAX_LOTS_PER_MESSAGE = 10


def lot_price(lot: Lot) -> Decimal | None:
    return lot.deal_price if lot.deal_price is not None else lot.current_price


def matches(sub: Subscription, lot: Lot) -> bool:
    title = (lot.title or "").lower()
    if sub.source_ids and lot.source_id not in sub.source_ids:
        return False
    if sub.keywords and not any(k.strip().lower() in title for k in sub.keywords if k.strip()):
        return False
    if any(k.strip() and k.strip().lower() in title for k in sub.exclude_keywords or []):
        return False
    if sub.tags:
        values = {str(v) for v in (lot.tags or {}).values() if v} | {
            str(x) for v in (lot.tags or {}).values() if isinstance(v, list) for x in v
        }
        if not any(t in values for t in sub.tags):
            return False
    price = lot_price(lot)
    if sub.min_price is not None and (price is None or price < sub.min_price):
        return False
    if sub.max_price is not None and (price is None or price > sub.max_price):
        return False
    return True


def _fmt_price(lot: Lot, price: Decimal | None = None) -> str:
    price = lot_price(lot) if price is None else price
    if price is None:
        return "-"
    return f"{lot.currency + ' ' if lot.currency and lot.currency != 'CNY' else '¥'}{price:,.2f}".replace(".00", "")


def _line(lot: Lot, source_name: str, extra: str = "") -> str:
    end = f"，结束 {lot.end_time.strftime('%m-%d %H:%M')} UTC" if lot.end_time else ""
    return f"· [{source_name}] {lot.title or lot.unique_key}  {_fmt_price(lot)}{extra}{end}\n  {lot.url or ''}"


async def _send(session: AsyncSession, sub: Subscription, event: str, rows: list[tuple[Lot, str, str]]) -> int:
    """rows: (拍品, 去重键, 附加说明)。已通知过的跳过，其余合并为一条消息推送。"""
    fresh: list[tuple[Lot, Notification, str]] = []
    for lot, dedupe, extra in rows:
        n = Notification(subscription_id=sub.id, lot_id=lot.id, event=event, dedupe_key=dedupe, message="")
        try:
            async with session.begin_nested():
                session.add(n)
        except IntegrityError:
            continue  # 已通知过
        fresh.append((lot, n, extra))
    if not fresh:
        return 0
    names = dict((await session.execute(select(Source.id, Source.name))).all())
    lines = [_line(lot, names.get(lot.source_id, ""), extra) for lot, _, extra in fresh[:MAX_LOTS_PER_MESSAGE]]
    if len(fresh) > MAX_LOTS_PER_MESSAGE:
        lines.append(f"……另有 {len(fresh) - MAX_LOTS_PER_MESSAGE} 件")
    title = f"订阅「{sub.name}」{EVENT_LABEL[event]} {len(fresh)} 件"
    text = "\n".join(lines)
    kind = sub.webhook_type or await get_setting(session, "alert.webhook_type")
    url = sub.webhook_url or await get_setting(session, "alert.webhook_url")
    ok, detail = await send_webhook(kind, url, title, text)
    for _, n, _ in fresh:
        n.message = f"{title}\n{text}"[:4000]
        n.sent, n.error = ok, None if ok else detail[:500]
    await session.commit()
    return len(fresh)


async def notify_after_crawl(
    session: AsyncSession, new_ids: list[int], price_changes: list[tuple[int, Decimal | None, Decimal | None]]
) -> None:
    if not new_ids and not price_changes:
        return
    subs = (await session.scalars(select(Subscription).where(Subscription.enabled))).all()
    if not subs:
        return
    ids = set(new_ids) | {c[0] for c in price_changes}
    lots = {lot.id: lot for lot in (await session.scalars(select(Lot).where(Lot.id.in_(ids)))).all()}
    changes = {lot_id: (old, new) for lot_id, old, new in price_changes}
    for sub in subs:
        events = set(sub.events or [])
        if "new" in events:
            rows = [(lots[i], "", "") for i in new_ids if i in lots and matches(sub, lots[i])]
            await _send(session, sub, "new", rows)
        if "price_change" in events:
            rows = []
            for i, (old, new) in changes.items():
                lot = lots.get(i)
                if lot and matches(sub, lot):
                    rows.append((lot, str(new), f"（原 {_fmt_price(lot, old)}）"))
            await _send(session, sub, "price_change", rows)
        if "price_reach" in events and sub.target_price is not None:
            rows = [
                (lot, "", f"（≥ 目标 {sub.target_price:,.0f}）")
                for i, lot in lots.items()
                if lot_price(lot) is not None and lot_price(lot) >= sub.target_price and matches(sub, lot)
            ]
            await _send(session, sub, "price_reach", rows)


async def check_ending_soon(session: AsyncSession) -> int:
    """推送即将结束的拍品（由调度器每分钟调用），返回通知的拍品数。"""
    subs = [s for s in (await session.scalars(select(Subscription).where(Subscription.enabled))).all()
            if "ending_soon" in (s.events or [])]
    if not subs:
        return 0
    now = utcnow()
    window = max(s.ending_minutes for s in subs)
    lots = (await session.scalars(
        select(Lot).where(Lot.status == "live", Lot.end_time > now, Lot.end_time <= now + timedelta(minutes=window))
    )).all()
    total = 0
    for sub in subs:
        deadline = now + timedelta(minutes=sub.ending_minutes)
        rows: list[tuple[Lot, str, str]] = []
        for lot in lots:
            end = lot.end_time
            if end is not None and end <= deadline and matches(sub, lot):
                rows.append((lot, "", f"（约 {max(int((end - now).total_seconds() // 60), 0)} 分钟后结束）"))
        total += await _send(session, sub, "ending_soon", rows)
    return total
