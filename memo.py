#!/usr/bin/env python3
"""
备忘录管理脚本
功能：add / list / send / clear
数据存储：飞书多维表格 (LbLub4mKAaZfhUs5wxmcw2AJnyg / tbllqP1t5Gzphj5C)
定时推送：launchd 调度 (9:00, 12:00, 14:00, 15:00, 16:00, 17:00, 18:00)
分类自动识别：合同流程 / 付款流程
"""

import json
import os
import sys
import datetime
import urllib.request
import re
import logging

# ---- 路径 ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "memo.log")

# ---- 飞书配置 (支持环境变量) ----
APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a93dd4676ebb9cce")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "QEPxFxwVVfTfUX79sMCmwgGcf3GN5TuD")
OPENID = os.environ.get("FEISHU_OPENID", "ou_1ed3bc80383d0aeaf6563db1b29deab3")
BASE_URL = "https://open.feishu.cn/open-apis"

# ---- 多维表格配置 ----
APP_TOKEN = os.environ.get("MEMO_APP_TOKEN", "LbLub4mKAaZfhUs5wxmcw2AJnyg")
TABLE_ID = os.environ.get("MEMO_TABLE_ID", "tbllqP1t5Gzphj5C")

# ---- 采购待办表格配置 ----
PURCHASE_APP_TOKEN = os.environ.get("PURCHASE_APP_TOKEN", "CRFQbGHt1ayRCXsqWd0cRadknXb")
PURCHASE_TABLE_ID = os.environ.get("PURCHASE_TABLE_ID", "tblsJ2lprcsBjzTe")

# ---- 字段名称 (使用字段名而非 field_id) ----
FIELD_NAME_REMINDER = "提醒事项"
FIELD_NAME_DATE = "日期"
FIELD_NAME_CONTENT = "提醒内容"
FIELD_NAME_STATUS = "状态"
FIELD_NAME_SERIAL = "序号"

# ---- 分类选项 ----
OPTION_CONTRACT = "合同流程"
OPTION_PAYMENT = "付款流程"
OPTION_DELIVERY = "交付任务"

# ---- 日志 ----
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
log.addHandler(console)


def today_str():
    return datetime.datetime.now().strftime("%Y/%m/%d")


def now_time():
    return datetime.datetime.now().strftime("%H:%M")


def get_token():
    """获取飞书 tenant_access_token"""
    req_data = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/auth/v3/tenant_access_token/internal",
        data=req_data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    if result.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {result}")
    return result["tenant_access_token"]


def classify_content(text):
    """自动识别内容分类：交付任务 / 合同流程 / 付款流程"""
    text_lower = text.lower()
    # 交付相关关键词（优先级最高）
    delivery_keywords = ["备货", "交付", "发货", "送货", "托运", "物流", "收件", "派送", "到货", "签收"]
    # 付款相关关键词
    payment_keywords = ["付款", "支付", "账期", "发票", "报销", "货款", "尾款", "定金", "押金", "费用", "收款", "开票", "对账", "结算"]
    # 合同相关关键词
    contract_keywords = ["合同", "协议", "盖章", "签章", "签字", "签署", "协议", "法务", "合规", "审批流", "OA", "下单", "订单"]

    delivery_score = sum(1 for kw in delivery_keywords if kw in text_lower)
    payment_score = sum(1 for kw in payment_keywords if kw in text_lower)
    contract_score = sum(1 for kw in contract_keywords if kw in text_lower)

    if delivery_score > 0:
        return OPTION_DELIVERY
    elif payment_score > 0 and payment_score >= contract_score:
        return OPTION_PAYMENT
    elif contract_score > 0:
        return OPTION_CONTRACT
    else:
        return OPTION_CONTRACT  # 默认合同流程


def bitable_request(token, method, path, data=None, app_token=None):
    """发送请求到飞书多维表格 API"""
    if app_token is None:
        app_token = APP_TOKEN
    url = f"{BASE_URL}/bitable/v1/apps/{app_token}{path}"
    req_data = json.dumps(data, ensure_ascii=False).encode("utf-8") if data else None

    req = urllib.request.Request(
        url,
        data=req_data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
    return result


def bitable_search_all(token, path, data=None, app_token=None):
    """分页查询飞书多维表格所有记录（自动翻页）"""
    all_items = []
    page_token = None

    while True:
        query = dict(data) if data else {}
        if page_token:
            query["page_token"] = page_token
        query["page_size"] = 100

        result = bitable_request(token, "POST", path, query, app_token=app_token)
        if result.get("code") != 0:
            log.error(f"分页查询失败: {result}")
            return {"code": result.get("code", -1), "data": {"items": []}}

        items = result.get("data", {}).get("items", [])
        all_items.extend(items)

        has_more = result.get("data", {}).get("has_more", False)
        if not has_more:
            break
        page_token = result.get("data", {}).get("page_token")
        if not page_token:
            break

    return {"code": 0, "data": {"items": all_items}}


def cmd_add(text):
    """添加备忘录到飞书多维表格（写入序号最小的空字段记录）"""
    token = get_token()
    classification = classify_content(text)

    def extract_text(val):
        """从飞书文本字段提取纯文本"""
        if isinstance(val, str):
            return val
        elif isinstance(val, dict):
            return val.get("text", str(val))
        elif isinstance(val, list):
            return " ".join(extract_text(v) for v in val)
        return str(val)

    # 查找序号最小且提醒事项为空的记录
    result = bitable_search_all(token, f"/tables/{TABLE_ID}/records/search", {
        "sort": [{"field_name": "序号", "desc": False}],
    })

    if result.get("code") != 0:
        log.error(f"查询备忘录失败: {result}")
        print(f"错误: 查询失败 {result.get('msg', '')}")
        return False

    all_records = result.get("data", {}).get("items", [])
    target_record = None
    for record in all_records:
        fields = record.get("fields", {})
        reminder = extract_text(fields.get(FIELD_NAME_REMINDER, ""))
        if not reminder.strip():
            target_record = record
            break

    if target_record:
        # 更新已有的空记录
        record_id = target_record.get("record_id", "")
        serial = extract_text(fields.get(FIELD_NAME_SERIAL, ""))
        update_fields = {
            FIELD_NAME_REMINDER: text,
            FIELD_NAME_DATE: int(datetime.datetime.now().timestamp() * 1000),
            FIELD_NAME_CONTENT: classification,
            FIELD_NAME_STATUS: None,
        }
        update_result = bitable_request(token, "PUT", f"/tables/{TABLE_ID}/records/{record_id}", {
            "fields": update_fields
        })
        if update_result.get("code") != 0:
            log.error(f"更新备忘录失败: {update_result}")
            print(f"错误: 更新失败 {update_result.get('msg', '')}")
            return False
        log.info(f"更新备忘录成功: [{serial}] {text} -> {classification}")
        print(f"✓ 已添加: {text}")
        print(f"  序号: {serial}")
        print(f"  分类: {classification}")
        print(f"  日期: {today_str()}")
        return True
    else:
        # 没有空记录，创建新记录（插入到最上面）
        fields = {
            FIELD_NAME_REMINDER: text,
            FIELD_NAME_DATE: int(datetime.datetime.now().timestamp() * 1000),
            FIELD_NAME_CONTENT: classification,
            FIELD_NAME_STATUS: None,
        }
        create_result = bitable_request(token, "POST", f"/tables/{TABLE_ID}/records", {
            "fields": fields,
            "insert_after": ""
        })
        if create_result.get("code") != 0:
            log.error(f"创建备忘录失败: {create_result}")
            print(f"错误: 创建失败 {create_result.get('msg', '')}")
            return False
        record_id = create_result.get("data", {}).get("record", {}).get("record_id", "")
        log.info(f"创建备忘录成功: {text} -> {classification} [record_id={record_id}]")
        print(f"✓ 已添加: {text}")
        print(f"  分类: {classification}")
        print(f"  日期: {today_str()}")
        return True
    print(f"  日期: {today_str()}")
    return True


def cmd_list():
    """列出今天的所有备忘录"""
    token = get_token()

    # 获取所有记录，简单过滤
    result = bitable_search_all(token, f"/tables/{TABLE_ID}/records/search", {})

    if result.get("code") != 0:
        log.error(f"查询备忘录失败: {result}")
        print(f"错误: 查询失败 {result.get('msg', '')}")
        return

    records = result.get("data", {}).get("items", [])
    if not records:
        print("今天还没有备忘录。")
        return

    # 过滤出今天的记录
    today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + datetime.timedelta(days=1)
    today_records = []
    for record in records:
        fields = record.get("fields", {})
        date_val = fields.get(FIELD_NAME_DATE)
        if date_val:
            record_date = datetime.datetime.fromtimestamp(date_val / 1000)
            if today_start <= record_date < today_end:
                today_records.append(record)

    if not today_records:
        print("今天还没有备忘录。")
        return

    def extract_text(val):
        """从飞书文本字段提取纯文本"""
        if isinstance(val, str):
            return val
        elif isinstance(val, dict):
            return val.get("text", str(val))
        elif isinstance(val, list):
            return " ".join(extract_text(v) for v in val)
        return str(val)

    print(f"📝 备忘录 ({today_str()}):")
    for i, record in enumerate(today_records, 1):
        fields = record.get("fields", {})
        reminder = extract_text(fields.get(FIELD_NAME_REMINDER, ""))
        content_type = extract_text(fields.get(FIELD_NAME_CONTENT, ""))
        status = extract_text(fields.get(FIELD_NAME_STATUS, ""))

        # 格式化时间
        date_val = fields.get(FIELD_NAME_DATE)
        time_str = ""
        if date_val:
            dt = datetime.datetime.fromtimestamp(date_val / 1000)
            time_str = dt.strftime("%H:%M")

        status_mark = "✓" if status == "已完成" else "○"
        print(f"  {i}. [{status_mark}] {reminder}  [{content_type}] {time_str}")

    print(f"\n共 {len(today_records)} 条")


def cmd_send():
    """推送未完成的备忘录到飞书"""
    token = get_token()

    # 获取所有记录，本地过滤
    result = bitable_search_all(token, f"/tables/{TABLE_ID}/records/search", {})

    if result.get("code") != 0:
        log.error(f"查询未完成备忘录失败: {result}")
        print(f"错误: 查询失败 {result.get('msg', '')}")
        return

    def extract_text(val):
        """从飞书文本字段提取纯文本"""
        if isinstance(val, str):
            return val
        elif isinstance(val, dict):
            return val.get("text", str(val))
        elif isinstance(val, list):
            return " ".join(extract_text(v) for v in val)
        return str(val)

    all_records = result.get("data", {}).get("items", [])

    # 过滤出未完成的记录（状态不为"已完成"且提醒事项不为空）
    pending_records = []
    for record in all_records:
        fields = record.get("fields", {})
        reminder = extract_text(fields.get(FIELD_NAME_REMINDER, ""))
        status = extract_text(fields.get(FIELD_NAME_STATUS, ""))
        # 跳过提醒事项为空的记录
        if not reminder.strip():
            continue
        if status != "已完成":
            pending_records.append(record)

    if not pending_records:
        log.info("没有未完成的备忘录，跳过推送")
        print("没有待办事项需要推送。")
        return

    # 构建卡片消息
    now = datetime.datetime.now()
    header_text = f"📝 待办提醒 ({now.strftime('%m/%d %H:%M')})"

    lines = [f"共 **{len(pending_records)}** 条待办：\n"]
    for i, record in enumerate(pending_records, 1):
        fields = record.get("fields", {})
        reminder = extract_text(fields.get(FIELD_NAME_REMINDER, ""))
        content_type = extract_text(fields.get(FIELD_NAME_CONTENT, ""))
        status = extract_text(fields.get(FIELD_NAME_STATUS, "")) or "未处理"
        serial = extract_text(fields.get(FIELD_NAME_SERIAL, ""))

        lines.append(f"{i}. [{serial}] {reminder}")
        lines.append(f"   📋 {content_type} | 状态: {status}\n")

    body = "\n".join(lines).strip()

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_text},
            "template": "orange",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}},
            {"tag": "note", "elements": [
                {"tag": "plain_text", "content": f"推送时间: {now.strftime('%H:%M:%S')}"}
            ]},
        ],
    }

    send_feishu_card(token, card)
    log.info(f"待办推送成功: {len(pending_records)} 条")
    print(f"✓ 已推送 {len(pending_records)} 条待办事项到飞书")

    # 推送采购待办（已采购不为"是"的记录）
    send_purchase_reminder(token)


def send_purchase_reminder(token):
    """推送未采购的待办事项到飞书"""
    # 使用服务端 filter 直接过滤已采购不为"是"的记录，避免全量分页读取
    result = bitable_request(token, "POST", f"/tables/{PURCHASE_TABLE_ID}/records/search", {
        "filter": {
            "conjunction": "and",
            "conditions": [
                {"field_name": "已采购", "operator": "isNot", "value": ["是"]}
            ]
        },
        "page_size": 100,
    }, app_token=PURCHASE_APP_TOKEN)

    if result.get("code") != 0:
        log.error(f"查询采购待办失败: {result}")
        return

    def extract_text(val):
        if isinstance(val, str):
            return val
        elif isinstance(val, dict):
            # 人员字段: {"name": "张三", "id": "ou_xxx"}
            if "name" in val:
                return val["name"]
            # 文本字段: {"text": "xxx"}
            if "text" in val:
                return val["text"]
            # 富文本字段: {"type": 1, "value": [{"text": "xxx", "type": "text"}]}
            if "type" in val and "value" in val and isinstance(val["value"], list):
                return " ".join(
                    v.get("text", str(v)) for v in val["value"] if isinstance(v, dict)
                )
            return str(val)
        elif isinstance(val, list):
            return " ".join(extract_text(v) for v in val)
        return str(val)

    all_records = result.get("data", {}).get("items", [])

    # 过滤：已采购 != "是" 且 物料名称不为空
    pending = []
    for record in all_records:
        fields = record.get("fields", {})
        item_name = extract_text(fields.get("物料名称", "")).strip()
        if item_name:
            pending.append(record)

    if not pending:
        log.info("没有未采购的待办")
        print("没有未采购的待办事项。")
        # 推送"无采购需求"提示
        now = datetime.datetime.now()
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "📦 采购待办提醒"},
                "template": "green",
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "✅ **当前没有采购需求**\n\n所有物料已采购完成，暂无待办。"}},
                {"tag": "note", "elements": [
                    {"tag": "plain_text", "content": f"推送时间: {now.strftime('%H:%M:%S')}"}
                ]},
            ],
        }
        send_feishu_card(token, card)
        print("✓ 已推送'无采购需求'提示到飞书")
        return

    # 构建卡片消息
    now = datetime.datetime.now()
    header_text = f"📦 采购待办提醒 ({now.strftime('%m/%d %H:%M')})"

    lines = [f"共 **{len(pending)}** 条未采购：\n"]
    for i, record in enumerate(pending, 1):
        fields = record.get("fields", {})
        item_name = extract_text(fields.get("物料名称", ""))
        purchase_no = extract_text(fields.get("采购单号", ""))
        requester = extract_text(fields.get("需求人", ""))
        quantity = extract_text(fields.get("数量", ""))
        project = extract_text(fields.get("项目名称", ""))
        link = extract_text(fields.get("链接（如需）", ""))
        lines.append(f"{i}. {item_name}")
        if purchase_no:
            lines.append(f"   单号: {purchase_no}")
        if requester:
            lines.append(f"   需求人: {requester}")
        if quantity:
            lines.append(f"   数量: {quantity}")
        if project:
            lines.append(f"   项目: {project}")
        if link:
            lines.append(f"   链接: {link}")
        lines.append("")

    body = "\n".join(lines).strip()

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_text},
            "template": "red",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}},
            {"tag": "note", "elements": [
                {"tag": "plain_text", "content": f"推送时间: {now.strftime('%H:%M:%S')}"}
            ]},
        ],
    }

    send_feishu_card(token, card)
    log.info(f"采购待办推送成功: {len(pending)} 条")
    print(f"✓ 已推送 {len(pending)} 条未采购待办")


def send_feishu_card(token, card):
    """发送卡片消息到飞书"""
    body = {
        "receive_id": OPENID,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    req_data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/im/v1/messages?receive_id_type=open_id",
        data=req_data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    if result.get("code") != 0:
        raise RuntimeError(f"消息发送失败: {result}")
    return result


def cmd_clear():
    """清空今天的备忘录（将状态设为已完成）"""
    token = get_token()
    today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # 查询今天的记录
    filter_conditions = {
        "conjunction": "and",
        "conditions": [
            {
                "field_name": "日期",
                "operator": "is",
                "value": [int(today_start.timestamp() * 1000)]
            }
        ]
    }

    result = bitable_search_all(token, f"/tables/{TABLE_ID}/records/search", {
        "filter": filter_conditions,
    })

    if result.get("code") != 0:
        log.error(f"查询备忘录失败: {result}")
        print(f"错误: 查询失败 {result.get('msg', '')}")
        return

    records = result.get("data", {}).get("items", [])
    if not records:
        print("今天没有备忘录需要清空。")
        return

    # 批量更新状态为已完成
    updated = 0
    for record in records:
        record_id = record.get("record_id", "")
        update_result = bitable_request(token, "PUT", f"/tables/{TABLE_ID}/records/{record_id}", {
            "fields": {FIELD_NAME_STATUS: "已完成"}
        })
        if update_result.get("code") == 0:
            updated += 1

    log.info(f"备忘录已清空: {updated} 条标记为已完成")
    print(f"✓ 已将 {updated} 条备忘录标记为已完成。")


# ---- 命令：完成（根据序号尾号）----
def cmd_done(suffix):
    """根据序号尾号将备忘录标记为已完成"""
    if not suffix.isdigit() or len(suffix) != 4:
        print("错误: 序号尾号必须为4位数字")
        return False

    token = get_token()
    result = bitable_search_all(token, f"/tables/{TABLE_ID}/records/search", {})

    if result.get("code") != 0:
        log.error(f"查询备忘录失败: {result}")
        print(f"错误: 查询失败 {result.get('msg', '')}")
        return False

    def extract_text(val):
        """从飞书文本字段提取纯文本"""
        if isinstance(val, str):
            return val
        elif isinstance(val, dict):
            return val.get("text", str(val))
        elif isinstance(val, list):
            return " ".join(extract_text(v) for v in val)
        return str(val)

    all_records = result.get("data", {}).get("items", [])
    target_record = None

    for record in all_records:
        fields = record.get("fields", {})
        serial = extract_text(fields.get(FIELD_NAME_SERIAL, ""))
        if serial.endswith(suffix):
            target_record = record
            break

    if not target_record:
        print(f"未找到序号尾号为 {suffix} 的记录")
        return False

    record_id = target_record.get("record_id", "")
    fields = target_record.get("fields", {})
    reminder = extract_text(fields.get(FIELD_NAME_REMINDER, ""))
    serial = extract_text(fields.get(FIELD_NAME_SERIAL, ""))

    update_result = bitable_request(token, "PUT", f"/tables/{TABLE_ID}/records/{record_id}", {
        "fields": {FIELD_NAME_STATUS: "已完成"}
    })

    if update_result.get("code") != 0:
        log.error(f"更新状态失败: {update_result}")
        print(f"错误: 更新失败 {update_result.get('msg', '')}")
        return False

    log.info(f"已将 [{serial}] {reminder} 标记为已完成")
    print(f"✓ 已完成: [{serial}] {reminder}")
    return True


# ---- 命令：采购完成（根据采购单号）----
def cmd_purchase_done(purchase_no):
    """根据采购单号将采购记录标记为已采购"""
    token = get_token()

    # 采购单号是 AutoNumber 类型，filter 不支持 is 匹配
    # 改用 filter 过滤未采购的记录（数据量小），再本地匹配采购单号
    result = bitable_request(token, "POST", f"/tables/{PURCHASE_TABLE_ID}/records/search", {
        "filter": {
            "conjunction": "and",
            "conditions": [
                {"field_name": "已采购", "operator": "isNot", "value": ["是"]},
                {"field_name": "采购单号", "operator": "isNotEmpty", "value": []},
            ]
        },
        "page_size": 100,
    }, app_token=PURCHASE_APP_TOKEN)

    if result.get("code") != 0:
        log.error(f"查询采购记录失败: {result}")
        print(f"错误: 查询失败 {result.get('msg', '')}")
        return False

    def extract_text(val):
        if isinstance(val, str):
            return val
        elif isinstance(val, dict):
            return val.get("text", str(val))
        elif isinstance(val, list):
            return " ".join(extract_text(v) for v in val)
        return str(val)

    records = result.get("data", {}).get("items", [])
    target_records = []
    for record in records:
        fields = record.get("fields", {})
        no = extract_text(fields.get("采购单号", "")).strip()
        material = extract_text(fields.get("物料名称", "")).strip()
        if no == purchase_no:
            # 校验：物料名称不能为空，防止标记空记录
            if not material:
                print(f"⚠ 采购单 [{purchase_no}] 是空记录（无物料名称），拒绝标记")
                log.warning(f"采购单 {purchase_no} 是空记录，拒绝标记已采购")
                return False
            target_records.append(record)

    if not target_records:
        print(f"未找到采购单号为 {purchase_no} 的记录")
        return False

    updated = 0
    for record in target_records:
        record_id = record.get("record_id", "")
        update_result = bitable_request(token, "PUT",
            f"/tables/{PURCHASE_TABLE_ID}/records/{record_id}",
            {"fields": {"已采购": "是"}},
            app_token=PURCHASE_APP_TOKEN)
        if update_result.get("code") == 0:
            updated += 1

    log.info(f"采购单 {purchase_no} 已标记为已采购: {updated} 条")
    print(f"✓ 已将采购单 [{purchase_no}] 标记为已采购 ({updated} 条记录)")
    return True


# ---- 主入口 ----
def main():
    if len(sys.argv) < 2:
        print("用法: memo.py <add|list|send|clear|done|purchase_done> [text]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    try:
        if cmd == "add":
            if len(sys.argv) < 3:
                print("错误: add 需要指定内容")
                sys.exit(1)
            text = " ".join(sys.argv[2:])
            cmd_add(text)
        elif cmd == "list":
            cmd_list()
        elif cmd == "send":
            cmd_send()
        elif cmd == "clear":
            cmd_clear()
        elif cmd == "done":
            if len(sys.argv) < 3:
                print("错误: done 需要指定序号尾号(4位)")
                sys.exit(1)
            suffix = sys.argv[2]
            cmd_done(suffix)
        elif cmd == "purchase_done":
            if len(sys.argv) < 3:
                print("错误: purchase_done 需要指定采购单号")
                sys.exit(1)
            purchase_no = sys.argv[2]
            cmd_purchase_done(purchase_no)
        else:
            print(f"未知命令: {cmd}")
            print("可用命令: add, list, send, clear, done, purchase_done")
            sys.exit(1)
    except Exception as e:
        log.error(f"执行失败: {e}", exc_info=True)
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
