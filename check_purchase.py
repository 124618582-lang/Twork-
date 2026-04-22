#!/usr/bin/env python3
import urllib.request, json, sys

TOKEN = 't-g1044mgzS235C6SABEWUXEGEKOY5BFBNL3BECDDY'
APP_TOKEN = 'CRFQbGHt1ayRCXsqWd0cRadknXb'
TABLE_ID = 'tblsJ2lprcsBjzTe'

def api_call(path, body):
    url = f'https://open.feishu.cn/open-apis{path}'
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        'Authorization': f'Bearer {TOKEN}',
        'Content-Type': 'application/json'
    }, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

total = 0
not_purchased = 0
page_token = None
page_count = 0

while True:
    page_count += 1
    body = {'page_size': 100}
    if page_token:
        body['page_token'] = page_token

    result = api_call(f'/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/search', body)

    if result.get('code') != 0:
        print(f'查询失败: {result}')
        break

    items = result['data']['items']
    total += len(items)

    page_not_purchased = 0
    for item in items:
        fields = item.get('fields', {})
        is_purchased = fields.get('已采购', '')
        if is_purchased != '是':
            not_purchased += 1
            page_not_purchased += 1

    has_more = result['data'].get('has_more', False)
    page_token = result['data'].get('page_token', '')
    print(f'第{page_count}页: {len(items)} 条, 未采购={page_not_purchased}, has_more={has_more}')

    if not has_more:
        break

print(f'')
print(f'===== 统计 =====')
print(f'总计: {total} 条')
print(f'未采购: {not_purchased} 条')
print(f'已采购: {total - not_purchased} 条')
