"""Small bilingual lexical retriever. Source text is local, versioned demo policy."""
import hashlib
import json
import math
import re
from .policy import KNOWLEDGE, normalize

STOP_WORDS = set("a an the i me my we our you your it its this that is are was were be been do does did to for of in on at by and or with as from if please can could would should what which how where when have has had will may need information provide customer order agent through original channel before after ask check request help thank thanks".split())

DOCUMENTS = {
    **{key: {**value, "auto_approved": True} for key, value in KNOWLEDGE.items()},
    "refund-process": {
        "category": "order_cancellation", "version": "demo-2026-09-v1", "auto_approved": False,
        "title": "退款与取消请求 / Refund and cancellation review",
        "aliases": ["退款", "取消订单", "退货", "refund", "cancel", "return order"],
        "zh": "退款或取消请求需要客服人工核实订单与履约状态。可请客户通过原订单渠道提供订单编号和退款原因；不要索取密码、验证码或完整银行卡号。核实前不得承诺退款已批准、金额或到账日期。",
        "en": "Refund or cancellation requests require an agent to verify the order and fulfillment status. Ask for an order reference and reason through the original order channel. Never request passwords, verification codes or full card details. Do not promise approval, an amount or a payment date before verification.",
    },
    "damage-process": {
        "category": "shipping_exception", "version": "demo-2026-09-v1", "auto_approved": False,
        "title": "包裹损坏、丢失与延误 / Shipment exceptions",
        "aliases": ["损坏", "破损", "丢失", "延误", "damaged", "lost", "delay", "broken"],
        "zh": "物流异常需要客服核实承运商记录。可请客户保留外包装和损坏照片，并通过原订单渠道提供订单编号与异常描述；不得在核实前承诺赔偿、补发或具体处理期限。",
        "en": "An agent must check carrier records for shipment exceptions. Ask the customer to retain packaging and damage photos and provide the order reference and issue description through the original order channel. Do not promise compensation, a replacement or a deadline before verification.",
    },
    "invoice-process": {
        "category": "billing_invoice", "version": "demo-2026-09-v1", "auto_approved": False,
        "title": "账单与发票核实 / Invoice review",
        "aliases": ["发票", "账单", "invoice", "billing", "receipt"],
        "zh": "发票与账单请求由客服核对订单后处理。请通过原订单渠道说明订单编号和所需发票类型；不要在普通邮件中提供完整银行卡信息。未核实前不得承诺已开票、税额或付款结果。",
        "en": "An agent checks the order before handling invoice or billing requests. Ask for the order reference and invoice type through the original order channel. Do not collect full payment-card details by email or claim an invoice, tax amount or payment result has been verified.",
    },
    "quote-process": {
        "category": "pricing_inquiry", "version": "demo-2026-09-v1", "auto_approved": False,
        "title": "运费报价信息采集 / Shipping quotation requirements",
        "aliases": ["报价", "运费", "价格", "quote", "quotation", "price", "shipping cost"],
        "zh": "运费报价需要人工核实运输路线、货物类型、重量体积和运输方式。可询问缺失信息并转客服处理；本知识库不含实时价格，不得自行生成报价、折扣或价格有效期。",
        "en": "A shipping quotation requires an agent to verify the route, cargo type, weight/volume and transport method. Ask for missing information and escalate. This knowledge base contains no current prices; do not invent prices, discounts or quote-validity periods.",
    },
}


def tokens(text):
    text = normalize(text)
    english = [word for word in re.findall(r"[a-z0-9]+", text) if word not in STOP_WORDS]
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    return set(english + [s[i:i+2] for s in chinese for i in range(len(s)-1)])


def search(query, categories=(), limit=3):
    """IDF-weighted term overlap plus exact aliases; category only boosts a text hit."""
    terms = tokens(query)
    corpus = {key: tokens(doc["title"] + " " + " ".join(doc["aliases"]) + " " + doc["zh"] + " " + doc["en"]) for key, doc in DOCUMENTS.items()}
    hits = []
    for key, doc in DOCUMENTS.items():
        aliases = any(normalize(alias) in normalize(query) for alias in doc["aliases"])
        overlap = terms & corpus[key]
        weight = sum(math.log(1 + len(corpus) / (1 + sum(term in ts for ts in corpus.values()))) for term in overlap)
        # Avoid matching every policy just because the message mentions an order.
        if not aliases and (len(overlap) < 2 or weight < 2.2):
            continue
        score = weight / max(1, math.sqrt(len(terms))) + (3 if aliases else 0) + (.5 if doc["category"] in categories else 0)
        source = {"id": key, **doc}
        source["sha256"] = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        source["score"] = round(score, 4)
        hits.append(source)
    return sorted(hits, key=lambda x: (-x["score"], x["id"]))[:limit]
