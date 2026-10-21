from fastapi import APIRouter

from app.api.deps import SessionDep
from app.notify import send_webhook
from app.schemas.api import AlertSettings, CrawlerSettings
from app.settings_store import get_settings_dict, set_setting

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/alert", response_model=AlertSettings)
async def get_alert(session: SessionDep) -> AlertSettings:
    s = await get_settings_dict(session, "alert.")
    return AlertSettings(**{k.removeprefix("alert."): v for k, v in s.items()})


@router.put("/alert", response_model=AlertSettings)
async def put_alert(body: AlertSettings, session: SessionDep) -> AlertSettings:
    for k, v in body.model_dump().items():
        await set_setting(session, f"alert.{k}", v)
    await session.commit()
    return body


@router.post("/alert/test")
async def test_alert(body: AlertSettings) -> dict:
    ok, detail = await send_webhook(body.webhook_type, body.webhook_url, "测试通知", "这是一条来自拍卖采集平台的测试消息")
    return {"ok": ok, "message": detail}


@router.get("/crawler", response_model=CrawlerSettings)
async def get_crawler(session: SessionDep) -> CrawlerSettings:
    s = await get_settings_dict(session, "crawler.")
    return CrawlerSettings(**{k.removeprefix("crawler."): v for k, v in s.items()})


@router.put("/crawler", response_model=CrawlerSettings)
async def put_crawler(body: CrawlerSettings, session: SessionDep) -> CrawlerSettings:
    body.proxies = [p.strip() for p in body.proxies if p.strip()]
    for k, v in body.model_dump().items():
        await set_setting(session, f"crawler.{k}", v)
    await session.commit()
    return body
