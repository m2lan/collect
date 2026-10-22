from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import SessionDep
from app.models import RuleTemplate
from app.schemas.api import TemplateIn, TemplateOut

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("/builtin")
async def builtin_templates() -> list[dict]:
    """经过真实站点验证的内置平台模板。"""
    from app.builtin.templates import BUILTIN_TEMPLATES

    return BUILTIN_TEMPLATES


@router.get("", response_model=list[TemplateOut])
async def list_templates(session: SessionDep) -> list[RuleTemplate]:
    return list((await session.scalars(select(RuleTemplate).order_by(RuleTemplate.id.desc()))).all())


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(body: TemplateIn, session: SessionDep) -> RuleTemplate:
    tpl = RuleTemplate(
        name=body.name, description=body.description, fetch_mode=body.fetch_mode,
        config=body.config.model_dump(by_alias=True),
    )
    session.add(tpl)
    await session.commit()
    return tpl


@router.put("/{template_id}", response_model=TemplateOut)
async def update_template(template_id: int, body: TemplateIn, session: SessionDep) -> RuleTemplate:
    tpl = await session.get(RuleTemplate, template_id)
    if tpl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板不存在")
    tpl.name, tpl.description, tpl.fetch_mode = body.name, body.description, body.fetch_mode
    tpl.config = body.config.model_dump(by_alias=True)
    await session.commit()
    return tpl


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: int, session: SessionDep) -> None:
    tpl = await session.get(RuleTemplate, template_id)
    if tpl:
        await session.delete(tpl)
        await session.commit()
