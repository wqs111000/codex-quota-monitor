"""Collect OpenAI organization costs through the official API."""
import json
import math
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc)


def threshold(name, default):
    try:
        value = float(os.environ.get(name, default))
        return value if math.isfinite(value) and value >= 0 else default
    except (TypeError, ValueError):
        return default


class APIMonitor:
    def __init__(self, data_dir, enqueue):
        self.path = Path(data_dir) / 'api-monitor.json'
        self.enqueue = enqueue
        self.lock = threading.RLock()
        self.key = os.environ.get('OPENAI_ADMIN_KEY', '').strip()
        self.cost_limit = threshold('OPENAI_COST_ALERT_USD', 90)
        self.requests_limit = threshold('OPENAI_REQUEST_ALERT_COUNT', 100)
        self.cost_error = None
        self.detail_error = None
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            self.state = saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            self.state = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent, delete=False) as stream:
                name = stream.name
                json.dump(self.state, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    def status(self):
        with self.lock:
            result = dict(self.state)
            result.update(configured=bool(self.key), cost_error=self.cost_error,
                          cost_threshold=self.cost_limit, detail_error=self.detail_error,
                          requests_threshold=self.requests_limit)
            for name in ('cost', 'details'):
                sample = result.get(name)
                if isinstance(sample, dict):
                    sample = dict(sample)
                    try:
                        sample['stale'] = (utcnow() - datetime.fromisoformat(sample['captured_at'])).total_seconds() > 2400
                    except (KeyError, ValueError, TypeError):
                        sample['stale'] = True
                    result[name] = sample
            return result

    def fetch_cost(self):
        now = utcnow()
        start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        params = {'start_time': int(start.timestamp()), 'limit': 31}
        total, pages = 0.0, set()
        while True:
            url = 'https://api.openai.com/v1/organization/costs?' + urllib.parse.urlencode(params)
            request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json'})
            with urllib.request.urlopen(request, timeout=15) as response:
                page = json.load(response)
            if not isinstance(page, dict) or not isinstance(page.get('data'), list):
                raise ValueError('invalid costs response')
            for bucket in page['data']:
                for result in bucket['results']:
                    amount = result.get('amount')
                    if amount is None:
                        continue
                    value = amount.get('value')
                    if amount.get('currency') != 'usd' or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        raise ValueError('invalid currency or cost')
                    total += value
            if not page.get('has_more'):
                break
            cursor = page.get('next_page')
            if not isinstance(cursor, str) or not cursor or cursor in pages or len(pages) >= 100:
                raise ValueError('invalid cursor')
            pages.add(cursor)
            params['page'] = cursor
        return total, start.strftime('%Y-%m'), now.isoformat(timespec='seconds')

    def fetch_pages(self, path, params):
        pages, buckets = set(), []
        while True:
            url = 'https://api.openai.com/v1/' + path + '?' + urllib.parse.urlencode(params, doseq=True)
            request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json'})
            with urllib.request.urlopen(request, timeout=15) as response:
                page = json.load(response)
            if not isinstance(page, dict) or not isinstance(page.get('data'), list):
                raise ValueError('invalid response')
            buckets.extend(page['data'])
            if not page.get('has_more'):
                return buckets
            cursor = page.get('next_page')
            if not isinstance(cursor, str) or not cursor or cursor in pages or len(pages) >= 100:
                raise ValueError('invalid cursor')
            pages.add(cursor)
            params['page'] = cursor

    def collect_details(self):
        now = utcnow()
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        cost_buckets = self.fetch_pages('organization/costs', {
            'start_time': int(month_start.timestamp()), 'limit': 31, 'group_by[]': 'api_key_id'})
        today = 0.0
        by_key = {}
        for bucket in cost_buckets:
            bucket_time = int(bucket['start_time'])
            for entry in bucket['results']:
                amount = entry.get('amount')
                if amount is None:
                    continue
                value = amount.get('value')
                if amount.get('currency') != 'usd' or isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                    raise ValueError('invalid cost')
                if bucket_time >= int(day_start.timestamp()):
                    today += value
                key_id = entry.get('api_key_id') or '未归属'
                if not isinstance(key_id, str):
                    raise ValueError('invalid key id')
                by_key[key_id] = by_key.get(key_id, 0.0) + value
        minute = now.replace(second=0, microsecond=0)
        usage = self.fetch_pages('organization/usage/completions', {
            'start_time': int((minute - timedelta(hours=1)).timestamp()),
            'end_time': int(minute.timestamp()), 'bucket_width': '1m', 'limit': 60})
        requests = 0
        for bucket in usage:
            for entry in bucket['results']:
                count = entry.get('num_model_requests')
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ValueError('invalid request count')
                requests += count
        captured = now.isoformat(timespec='seconds')
        with self.lock:
            if self.requests_limit > 0 and requests >= self.requests_limit:
                hour = now.strftime('%Y-%m-%dT%H')
                self.enqueue('api-requests:' + hour,
                             f'OpenAI API 最近约 1 小时请求 {requests} 次，达到提醒阈值 {int(self.requests_limit)} 次', captured)
            self.state['details'] = {'today': today, 'requests_hour': requests,
                'key_costs': [{'id': key, 'amount': amount} for key, amount in sorted(by_key.items(), key=lambda item: item[1], reverse=True)],
                'captured_at': captured}
            self.save()
            self.detail_error = None

    def collect(self):
        if not self.key:
            return
        try:
            total, month, captured = self.fetch_cost()
            with self.lock:
                if total >= self.cost_limit:
                    self.enqueue('api-cost:' + month, f'OpenAI API 本月花费 ${total:.2f}，已达到提醒阈值 ${self.cost_limit:.2f}（UTC 月份）', captured)
                self.state['cost'] = {'amount': total, 'month': month, 'captured_at': captured}
                self.save()
                self.cost_error = None
        except urllib.error.HTTPError as error:
            self.cost_error = f'花费接口返回 HTTP {error.code}，请检查管理员密钥及权限'
        except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
            self.cost_error = '花费读取失败，请检查网络与管理员密钥权限'
        try:
            self.collect_details()
        except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
            self.detail_error = 'API 用量明细读取失败，请检查网络与管理员密钥权限'

    def run(self):
        while True:
            try:
                self.collect()
            except Exception:
                self.cost_error = '花费采集或提醒保存失败，请检查本机配置'
            time.sleep(900)
