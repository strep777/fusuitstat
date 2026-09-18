"""开发演示数据。

用途：让前端与联调环境有真实结构的内容可渲染。所有条目均为演示数据，
正文中标注「演示内容」，不得直接用于生产。生产内容由运营在后台编辑、预览后发布。

用法：``uv run python -m app.cli seed-demo``
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.catalog.models import Character, Product, Sku
from app.modules.content import repository as content_repo
from app.modules.content.models import ContentEntry, SeriesSpec
from app.modules.content.service import ensure_default_settings
from app.modules.identity.permissions import sync_catalog
from app.platform.ids import new_id, now_ms

DEMO_MARK = "（演示内容）"


def _p(text: str) -> str:
    return f"<p>{text}</p>"


def _ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


CONTENT: tuple[dict[str, Any], ...] = (
    {
        "kind": "page",
        "slug": "about",
        "title": f"关于弈星纪元{DEMO_MARK}",
        "summary": "一支以手工制作为核心的兽装工作室。",
        "body": _p(
            "这段文字是演示数据，用于验证页面结构与状态，不代表工作室的正式介绍。"
            "正式内容由运营在后台编辑、预览后发布。"
        )
        + _p("工作方式：先确认可接范围与设定授权，再进入报价与合同；制作过程按阶段交付图片与确认记录。"),
    },
    {
        "kind": "page",
        "slug": "care",
        "title": f"养护与清洁{DEMO_MARK}",
        "summary": "穿戴、清洁、运输与存放的基本做法。",
        "body": _p("以下内容为演示，正式说明必须与实际使用的材料一致。")
        + _ul(
            [
                "穿戴：从脚部开始，避免拉扯缝合线；佩戴头部时先固定内衬。",
                "清洁：按材料选择手洗或局部清洁，避免高温烘干与强碱清洁剂。",
                "运输：使用硬壳箱或支撑填充，头部单独固定。",
                "存放：阴凉干燥、避免长期受压，定期通风。",
            ]
        ),
    },
    {
        "kind": "legal",
        "slug": "terms",
        "title": "用户服务协议",
        "summary": "演示版本，正式条款由经营主体审核后发布。",
        "body": _p(
            "这是一份演示条款，用于验证法律文本页面的版本号与生效时间展示。"
            "正式条款将在经营主体完成审核后由运营发布，发布时记录版本号与生效时间。"
        )
        + _ul(
            [
                "服务范围：本网站提供内容浏览、委托询价、商品购买、订单查询与售后申请。",
                "合同与证据：报价接受与付款前会展示经营主体、范围、金额、交付安排与售后规则，同意记录按版本留存。",
                "争议处理：争议解决方式以正式条款为准。",
            ]
        ),
    },
    {
        "kind": "legal",
        "slug": "privacy",
        "title": "隐私政策",
        "summary": "最小化收集；说明短信、支付、存储与监控服务的数据用途。",
        "body": _p("演示版本。正式隐私政策由经营主体审核后发布。")
        + _p("收集范围：浏览不要求登录；询价只收集制作必需的角色信息，量体数据在接单后按必要范围收集。")
        + _ul(
            [
                "手机号：用于登录与订单通知，加密保存，日志与后台列表只显示脱敏号码。",
                "设定图与量体资料：私密保存，仅本人与必要岗位可读，不进入公开 CDN。",
                "第三方服务：短信、支付、对象存储与监控服务商会列入处理清单。",
                "你的权利：可申请访问、复制、更正、删除、撤回展示授权与注销账号。",
            ]
        ),
    },
    {
        "kind": "legal",
        "slug": "returns",
        "title": "取消与退换规则",
        "summary": "区分定制与现货；质量问题走独立处理流程。",
        "body": _p("演示版本，正式规则由经营主体审核后发布。")
        + _ul(
            [
                "定制商品：按制作阶段与已确认政策处理取消；质量问题不因定制属性而免除责任。",
                "现货与配件：参照淘宝 / 闲鱼平台规则执行退换。",
                "跨境订单：物流、关税与偏远地区附加费单独说明。",
                "退款：按原支付渠道退回，到账时间以渠道为准。",
            ]
        ),
    },
    {
        "kind": "legal",
        "slug": "points",
        "title": "星绒币规则",
        "summary": "获取方式、发放时间、调整与申诉。",
        "body": _p("演示版本。数值由后台配置，页面展示与服务端校验使用同一份配置。")
        + _ul(
            [
                "获取：每实付 5 元获得 1 星绒币，运费不计入；单笔无上限。",
                "发放：订单完成交付后 7 天且无未决售后时入账。",
                "退款：退款到达后按最新净额重算，可能产生负向调整。",
                "用途：兑换礼品、商品与订单抵扣（启用前另行公布兑换率与上限）。",
                "不可用于：充值、提现、转账，也不能替代现金退款。",
                "申诉：对流水有异议可在星绒币页面提出申诉。",
            ]
        ),
    },
    {
        "kind": "series",
        "slug": "full-suit",
        "title": "全装定制",
        "summary": "包含头部、身体、手部与脚部的完整兽装，适合演出与展会。",
        "body": _p("参数以报价记录为准；起价仅表示该系列的入门配置。")
        + _ul(["适合场景：展会、演出、长期角色扮演", "可选结构：可拆卸尾巴、内置风扇、可换手套"]),
        "spec": {
            "code": "FULL",
            "base_price_cents": 1_800_000,
            "price_includes": ["头部", "身体", "手部", "脚部", "出厂白棚照", "一年保修"],
            "price_excludes": ["跨境关税", "加急排期", "额外配件", "偏远地区运费"],
            "lead_time_note": "交期按报价记录；排期后按阶段确认推进。",
            "spec_schema": {
                "头骨": "一体填充",
                "内衬": "全身内衬",
                "风扇": "头内风扇",
                "保修": "一年",
                "清洗服务": "按合同约定",
            },
            "display_order": 1,
        },
    },
    {
        "kind": "series",
        "slug": "half-suit",
        "title": "半装定制",
        "summary": "头部、手部与脚部或身体任选，兼顾预算与还原度。",
        "body": _p("半装适合先体验实装效果，后续可加做其余部件。"),
        "spec": {
            "code": "HALF",
            "base_price_cents": 1_200_000,
            "price_includes": ["头部", "手部", "出厂照", "一年保修"],
            "price_excludes": ["身体部件", "脚部", "跨境关税", "加急排期"],
            "lead_time_note": "交期按报价记录。",
            "spec_schema": {"头骨": "一体填充", "内衬": "头部内衬", "风扇": "可选", "保修": "一年"},
            "display_order": 2,
        },
    },
    {
        "kind": "series",
        "slug": "head-only",
        "title": "单头定制",
        "summary": "只做头部，适合已有身体或预算优先的用户。",
        "body": _p("单头可与已有装体搭配，需要注意尺寸与配色衔接。"),
        "spec": {
            "code": "HEAD",
            "base_price_cents": 680_000,
            "price_includes": ["头部", "出厂照", "半年保修"],
            "price_excludes": ["身体", "手部", "脚部", "清洗服务"],
            "lead_time_note": "交期按报价记录。",
            "spec_schema": {"头骨": "定制", "内衬": "头部内衬", "风扇": "可选", "保修": "半年"},
            "display_order": 3,
        },
    },
    {
        "kind": "series",
        "slug": "accessory",
        "title": "配件与伪全",
        "summary": "尾巴、手套、脚骨等配件，以及伪全方案。",
        "body": _p("配件可单独申请；需要人工确认尺寸与配色。"),
        "spec": {
            "code": "PART",
            "base_price_cents": 120_000,
            "price_includes": ["单个配件", "基础配色"],
            "price_excludes": ["主体装体", "跨境关税", "清洗服务"],
            "lead_time_note": "按配件类型分别确认。",
            "spec_schema": {"类型": "尾巴 / 手套 / 脚骨", "保修": "三个月"},
            "display_order": 4,
        },
    },
    {
        "kind": "faq",
        "slug": "how-to-inquire",
        "title": "怎么开始一次委托？",
        "summary": "先阅读委托指南，再提交询价。",
        "body": _p(
            "登录后进入「提交询价」，填写角色、物种、系列与制作要求，上传 1–3 张设定图。"
            "资料不足时工作室会说明需要补什么，补齐后进入报价。"
        ),
    },
    {
        "kind": "faq",
        "slug": "quote-validity",
        "title": "报价有效期是多久？",
        "summary": "默认 7 天，由后台配置。",
        "body": _p(
            "报价有版本与有效期。过期后需要重新评估；如果中途调整了内容，会发布新版本，"
            "旧版本不能直接转成订单。"
        ),
    },
    {
        "kind": "faq",
        "slug": "prepayment",
        "title": "预付款是多少？尾款什么时候付？",
        "summary": "默认预付款 50%，尾款在发货前结清。",
        "body": _p(
            "默认预付款为总价的 50%，具体比例在报价中列明。尾款在交付前结清，"
            "未付清尾款不会发货。所有比例都由后台配置，不以客服口头说法为准。"
        ),
    },
    {
        "kind": "faq",
        "slug": "points",
        "title": "星绒币怎么获得？",
        "summary": "购买与有效反馈都可获得星绒币。",
        "body": _p(
            "每实付 5 元获得 1 星绒币，运费不计；订单完成交付后 7 天且无未决售后时入账。"
            "有效反馈按 5 / 20 / 50 星绒币分级奖励，每人每天最多奖励 1 条。"
        ),
    },
    {
        "kind": "faq",
        "slug": "minor",
        "title": "未成年可以委托吗？",
        "summary": "可以，但需要监护人同意并额外签署监护人条款。",
        "body": _p(
            "未成年用户提交付费委托需要监护人同意，并在合同中额外签署监护人条款。"
            "未完成监护人签署前不会进入收款环节。"
        ),
    },
    {
        "kind": "news",
        "slug": "demo-schedule-notice",
        "title": f"档期与名额说明{DEMO_MARK}",
        "summary": "关于申请、筛选与名额预留的说明。",
        "body": _p(
            "本节为演示公告：委托按轮次开放，先提交普通申请，工作室人工筛选后发布报价。"
            "接受报价时会预留名额用于付款，超时未付款会进入关闭核对。"
        ),
    },
    {
        "kind": "news",
        "slug": "demo-points-launch",
        "title": f"星绒币规则上线说明{DEMO_MARK}",
        "summary": "购买与反馈奖励规则、发放时间与申诉入口。",
        "body": _p("演示公告：星绒币用于奖励在官网购买商品与提交有效反馈，不可充值、提现或转账。"),
    },
    {
        "kind": "work",
        "slug": "demo-work-dual-star",
        "title": f"双曜弈兽 · 全装{DEMO_MARK}",
        "summary": "用于验证作品详情页结构的演示条目。",
        "body": _p("演示条目：正式作品需要客户单独授权后才可公开，包括客户身份与成品照片。"),
        "species": "龙",
        "work_type": "全装",
        "craft_notes": "演示工艺说明：可拆卸尾巴、头内风扇、全身内衬。",
        "designer_credit": "演示设定作者",
        "maker_credit": "演示装师",
        "photographer_credit": "演示摄影",
        "client_display_name": "演示委托人",
        "display_authorized": 1,
        "is_featured": 1,
    },
    {
        "kind": "work",
        "slug": "demo-work-twin-tail",
        "title": f"弈环双尾 · 半装{DEMO_MARK}",
        "summary": "用于验证筛选与网格布局的演示条目。",
        "body": _p("演示条目：作品与可售库存分开管理，作品展示不代表仍有库存。"),
        "species": "狐",
        "work_type": "半装",
        "designer_credit": "演示设定作者",
        "maker_credit": "演示装师",
        "display_authorized": 1,
    },
    {
        "kind": "work",
        "slug": "demo-work-star-dragon",
        "title": f"逐星绒龙 · 单头{DEMO_MARK}",
        "summary": "用于验证单头类型的演示条目。",
        "body": _p("演示条目。"),
        "species": "龙",
        "work_type": "单头",
        "maker_credit": "演示装师",
        "display_authorized": 1,
    },
    {
        "kind": "team",
        "slug": "demo-team",
        "title": f"团队成员{DEMO_MARK}",
        "summary": "演示条目：真实人名与照片必须取得授权后才可公开。",
        "body": _p("演示条目：团队成员信息在取得本人授权后由运营发布。"),
    },
)

PRODUCTS: tuple[dict[str, Any], ...] = (
    {
        "slug": "demo-tail-accessory",
        "kind": "stock_goods",
        "title": f"可拆卸尾巴配件{DEMO_MARK}",
        "subtitle": "现货配件，按配色分 SKU",
        "category": "accessory",
        "description": _p("演示商品：用于验证 SKU 选择、库存与结算流程。"),
        "highlights": ["可拆卸结构", "现货发货", "支持常规清洁"],
        "requires_approval": False,
        "skus": [
            ("TAIL-BLK", "曜黑", 32_000, 5),
            ("TAIL-AMB", "琥珀", 34_000, 2),
        ],
    },
    {
        "slug": "demo-care-service",
        "kind": "service",
        "title": f"兽装清洁与养护服务{DEMO_MARK}",
        "subtitle": "先申请，报价后履约",
        "category": "service",
        "service_kind": "cleaning",
        "description": _p("演示服务：先提交申请，工作室确认材质与可行性后报价。"),
        "highlights": ["人工确认材质", "先申请再报价"],
        "requires_approval": True,
        "skus": [("CARE-BASE", "标准清洁", 28_000, 10)],
    },
    {
        "slug": "demo-character-alcor",
        "kind": "exclusive_character",
        "title": f"角色 · 星绒雏龙{DEMO_MARK}",
        "subtitle": "独占角色，库存 1",
        "category": "character",
        "description": _p("演示角色商品：独占角色只能售出一次，库存固定为 1。"),
        "highlights": ["独占库存 1", "含基础授权说明"],
        "requires_approval": False,
        "skus": [("CHR-ALCOR", "全装成品", 2_400_000, 1)],
        "character": {
            "slug": "demo-alcor",
            "name": "星绒雏龙",
            "species": "龙",
            "designer_credit": "演示设定作者",
            "story": "演示故事：角色领养后按合同约定执行授权范围。",
            "rights_summary": "演示授权说明：授权范围按照合同要求执行；网站不展示转让与所有权变更相关内容。",
            "public_status": "available",
            "tags": ["龙", "独占"],
        },
    },
    {
        "slug": "demo-head-pad",
        "kind": "stock_goods",
        "title": f"头部支撑内衬{DEMO_MARK}",
        "subtitle": "现货周边",
        "category": "merch",
        "description": _p("演示商品：用于验证低库存提示与售罄状态。"),
        "highlights": ["透气内衬", "可拆洗"],
        "requires_approval": False,
        "skus": [("PAD-STD", "标准码", 9_800, 1)],
    },
)


def seed_demo(session: Session, *, force: bool = False) -> dict[str, Any]:
    """写入演示内容与目录数据。幂等：同 slug 已存在则跳过（``force`` 时更新正文）。"""
    sync_catalog(session)
    ensure_default_settings(session)

    content_created = 0
    content_updated = 0
    series_created = 0
    now = now_ms()

    for item in CONTENT:
        existing = session.scalars(
            sa.select(ContentEntry).where(
                ContentEntry.kind == item["kind"],
                ContentEntry.slug == item["slug"],
                ContentEntry.locale == "zh-CN",
            )
        ).one_or_none()
        payload = {
            "title": item["title"],
            "summary": item.get("summary", ""),
            "sanitized_body": item["body"],
        }
        if existing is None:
            entry = ContentEntry(
                id=new_id("content"),
                kind=item["kind"],
                slug=item["slug"],
                locale="zh-CN",
                status="published",
                publish_at=now,
                revision=1,
                tags=item.get("tags"),
                species=item.get("species"),
                work_type=item.get("work_type"),
                craft_notes=item.get("craft_notes"),
                designer_credit=item.get("designer_credit"),
                maker_credit=item.get("maker_credit"),
                photographer_credit=item.get("photographer_credit"),
                client_display_name=item.get("client_display_name"),
                display_authorized=item.get("display_authorized", 0),
                is_featured=item.get("is_featured", 0),
                sort_order=item.get("sort_order", 0),
                seo_title=item["title"],
                seo_description=item.get("summary", ""),
                **payload,
            )
            session.add(entry)
            session.flush()
            content_created += 1
        else:
            if force:
                for key, value in payload.items():
                    setattr(existing, key, value)
                existing.status = "published"
                existing.publish_at = existing.publish_at or now
                existing.version += 1
                existing.updated_at = now
                content_updated += 1
            entry = existing

        spec = item.get("spec")
        if spec and content_repo.get_series_for_entry(session, entry.id) is None:
            session.add(
                SeriesSpec(
                    id=new_id("content"),
                    entry_id=entry.id,
                    code=spec["code"],
                    base_price_cents=spec["base_price_cents"],
                    currency="CNY",
                    price_includes=spec.get("price_includes"),
                    price_excludes=spec.get("price_excludes"),
                    lead_time_note=spec.get("lead_time_note"),
                    spec_schema=spec.get("spec_schema"),
                    display_order=spec.get("display_order", 0),
                )
            )
            series_created += 1

    products_created = 0
    skus_created = 0
    characters_created = 0
    for item in PRODUCTS:
        product = session.scalars(sa.select(Product).where(Product.slug == item["slug"])).one_or_none()
        if product is None:
            product = Product(
                id=new_id("product"),
                slug=item["slug"],
                kind=item["kind"],
                title=item["title"],
                subtitle=item.get("subtitle", ""),
                description=item.get("description", ""),
                highlights=item.get("highlights"),
                category=item.get("category"),
                service_kind=item.get("service_kind"),
                requires_approval=1 if item.get("requires_approval") else 0,
                status="published",
                sort_order=10,
            )
            session.add(product)
            session.flush()
            products_created += 1
        for code, label, price, stock in item["skus"]:
            if session.scalars(sa.select(Sku).where(Sku.sku_code == code)).one_or_none() is not None:
                continue
            session.add(
                Sku(
                    id=new_id("sku"),
                    product_id=product.id,
                    sku_code=code,
                    label=label,
                    price_cents=price,
                    currency="CNY",
                    stock_on_hand=stock,
                    reserved=0,
                    max_per_order=1 if item["kind"] == "exclusive_character" else 5,
                )
            )
            skus_created += 1
        character = item.get("character")
        if character and session.scalars(
            sa.select(Character).where(Character.slug == character["slug"])
        ).one_or_none() is None:
            session.add(
                Character(
                    id=new_id("character"),
                    product_id=product.id,
                    slug=character["slug"],
                    name=character["name"],
                    species=character.get("species", ""),
                    designer_credit=character.get("designer_credit"),
                    story=character.get("story", ""),
                    rights_summary=character.get("rights_summary", ""),
                    public_status=character.get("public_status", "available"),
                    exclusive=1,
                    tags=character.get("tags"),
                    display_authorized=1,
                )
            )
            characters_created += 1

    session.flush()
    return {
        "content_created": content_created,
        "content_updated": content_updated,
        "series_specs_created": series_created,
        "products_created": products_created,
        "skus_created": skus_created,
        "characters_created": characters_created,
        "note": "演示数据仅供开发与联调；正式内容由运营在后台发布。",
    }
