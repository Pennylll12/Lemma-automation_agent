"""Configured SleekFlow Flow-trigger callback; never infer credential/header names."""
from datetime import datetime
import hashlib
import json
import os
import re
from urllib.parse import urlsplit

from backend.calendar_service import HK


class ReplyWebhookConfigError(RuntimeError):
    pass


def enabled():
    return os.getenv('SLEEKFLOW_REPLY_WEBHOOK_ENABLED', 'false').lower() == 'true'


def settings():
    url = os.getenv('SLEEKFLOW_REPLY_WEBHOOK_URL', '').strip()
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError()
        headers = json.loads(os.getenv('SLEEKFLOW_REPLY_WEBHOOK_HEADERS', '{}'))
        if not isinstance(headers, dict) or not headers:
            raise ValueError()
        reserved = {'host', 'content-length', 'content-type', 'transfer-encoding', 'idempotency-key'}
        for key, value in headers.items():
            if (not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                    or key.lower() in reserved or not isinstance(value, str) or not value.strip()
                    or any(ord(c) < 32 or ord(c) > 126 for c in value)):
                raise ValueError()
    except (ValueError, TypeError):
        raise ReplyWebhookConfigError('請配置 SleekFlow 回覆 Webhook 的 HTTPS URL 及官方範例中的 Headers JSON；未提交回覆') from None
    return url, headers


def request_data(job, reply, now):
    url, headers = settings()
    # IDs remain stable across duplicate receipts. Receivers should also de-duplicate event_id.
    event_id = 'supermama-reply-' + hashlib.sha256(job['id'].encode()).hexdigest()
    received = job.get('received_at') or job['created']
    payload = {
        'event': 'inspection.reply.ready', 'event_id': event_id,
        'message_id': job['id'], 'contact_id': job['contact_id'], 'phone': job['contact_id'],
        'channel': job['channel'], 'reply': reply,
        'received_at': datetime.fromtimestamp(received, HK).isoformat(),
        'processed_at': datetime.fromtimestamp(now, HK).isoformat(),
        'time_source': job.get('time_source') or 'legacy',
    }
    return url, {**headers, 'Idempotency-Key': event_id}, payload
