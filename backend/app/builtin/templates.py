"""内置规则模板：经过真实站点验证的常见拍卖平台规则，新建站点时可一键套用。"""

from typing import Any

TAOBAO_STATUS = {
    "ing": "live", "before": "upcoming", "after": "ended", "end": "ended", "done": "ended",
    "sold": "ended", "failed": "unsold", "revocation": "unsold", "suspend": "unsold",
}

BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "taobao-zichan-search",
        "name": "阿里资产 / 淘宝拍卖 · 资产搜索",
        "description": "阿里资产（zc-paimai.taobao.com）搜索结果页，在网址中修改 keyword 即可按关键词采集。"
        "示例：https://zc-paimai.taobao.com/wow/pm/default/pc/zichansearch?keyword=翡翠&page=1。"
        "页面接口带签名，使用浏览器截获接口数据。",
        "example_url": "https://zc-paimai.taobao.com/wow/pm/default/pc/zichansearch?keyword=%E7%BF%A1%E7%BF%A0&page=1",
        "fetch_mode": "browser",
        "config": {
            "fetch": {
                "mode": "browser", "timeout": 45, "wait_ms": 1000,
                "capture": r"mtop\.taobao\.datafront\.invoke\.auctionwalle", "capture_body": "getPageModulesData",
            },
            "list": {
                "item_selector": "$.data.data.GQL_getPageModulesData.*.items.schemeList[*]",
                "item_selector_type": "jsonpath",
                "unique_key": "lot_no",
                "fields": [
                    {"key": "lot_no", "type": "jsonpath", "selector": "$.itemId"},
                    {"key": "title", "type": "jsonpath", "selector": "$.auctionTitle", "transform": ["trim"]},
                    {"key": "current_price", "type": "template", "selector": "{price}{priceUnit}", "transform": ["price"]},
                    {"key": "url", "type": "template", "selector": "https:{auctionLink}"},
                    {"key": "image", "type": "template", "selector": "https:{pictureUrl}"},
                    {"key": "status", "type": "jsonpath", "selector": "$.status",
                     "transform": [{"name": "map", "mapping": TAOBAO_STATUS}]},
                    {"key": "time_text", "type": "jsonpath", "selector": "$.feetText", "transform": ["trim"]},
                    {"key": "end_time", "type": "regex", "from": "time_text", "pattern": "^预计(.+?)结束$", "transform": ["datetime"]},
                    {"key": "start_time", "type": "regex", "from": "time_text", "pattern": "^(?:起始|开拍)?(.+?)(?:开始|开拍)$", "transform": ["datetime"]},
                    {"key": "bid_count", "type": "jsonpath", "selector": "$.bidCnt", "transform": ["int"]},
                    {"key": "views", "type": "jsonpath", "selector": "$.pv", "transform": ["int"]},
                    {"key": "applicants", "type": "jsonpath", "selector": "$.applyCnt", "transform": ["int"]},
                    {"key": "seller", "type": "jsonpath", "selector": "$.shopName", "transform": ["trim"]},
                ],
            },
            "pagination": {"type": "page_param", "param": "page", "start": 1, "max_pages": 3},
        },
    },
    {
        "key": "allsurplus-search",
        "name": "AllSurplus · 列表 / 搜索",
        "description": "AllSurplus（Liquidity Services）列表页，如今日结束 /en/closing-today、新上架 /en/new-listings、"
        "搜索结果页。接口需要浏览器会话令牌，使用浏览器截获接口数据，点击翻页。",
        "example_url": "https://www.allsurplus.com/en/closing-today",
        "fetch_mode": "browser",
        "config": {
            "fetch": {"mode": "browser", "timeout": 60, "wait_ms": 1500, "capture": r"maestro\.lqdt1\.com/search/list"},
            "list": {
                "item_selector": "$.assetSearchResults[*]",
                "item_selector_type": "jsonpath",
                "unique_key": "lot_no",
                "fields": [
                    {"key": "lot_no", "type": "template", "selector": "{accountId}-{assetId}"},
                    {"key": "title", "type": "jsonpath", "selector": "$.assetShortDescription", "transform": ["trim"]},
                    {"key": "current_price", "type": "template", "selector": "{currentBid} {currencyCode}", "transform": ["price"]},
                    {"key": "url", "type": "template", "selector": "https://www.allsurplus.com/en/asset/{assetId}/{accountId}"},
                    {"key": "image", "type": "template", "selector": "https://webassets.lqdt1.com/assets/photos/{accountId}/{photo}"},
                    {"key": "status", "type": "jsonpath", "selector": "$.isSoldAuction",
                     "transform": [{"name": "map", "mapping": {"False": "live", "True": "ended"}}]},
                    {"key": "end_time", "type": "jsonpath", "selector": "$.assetAuctionEndDateUtc", "transform": ["datetime"]},
                    {"key": "category", "type": "jsonpath", "selector": "$.categoryDescription"},
                    {"key": "location", "type": "template", "selector": "{locationCity}, {locationState} {country}", "transform": ["trim"]},
                    {"key": "seller", "type": "jsonpath", "selector": "$.companyName"},
                    {"key": "make", "type": "jsonpath", "selector": "$.makebrand"},
                    {"key": "model", "type": "jsonpath", "selector": "$.model"},
                    {"key": "year", "type": "jsonpath", "selector": "$.modelYear"},
                ],
            },
            "pagination": {"type": "click", "selector": "li[data-bs-type=\"nextPage\"] a", "max_pages": 3},
        },
    },
]
