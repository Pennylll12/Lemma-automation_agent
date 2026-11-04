"""Read-only Google FreeBusy adapter and deterministic inspection candidates.

No event titles, descriptions, attendee data or write scopes are requested.
Candidates are not reservations; staffing, travel and meals still need review.
"""
import asyncio
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field, field_validator
from backend import audit_service as audit

HK = ZoneInfo('Asia/Hong_Kong')
STARTS = ('09:00', '12:30', '15:45', '18:00')
SCOPE = 'https://www.googleapis.com/auth/calendar.freebusy'


class CalendarError(Exception):
    """Safe, user-facing error. Never include provider bodies or credentials."""


class AvailabilityRequest(BaseModel):
    start_date: date
    days: int = Field(default=7, ge=1, le=14)
    duration_minutes: Literal[120, 150, 180] = 180
    pickup_at: datetime | None = None
    completion_date: date | None = None
    preference: Literal['any', 'morning', 'afternoon', 'evening'] = 'any'
    include_weekday_evening: bool = False
    plan_from_completion: bool = False
    weekend_only: bool = False

    @field_validator('pickup_at')
    @classmethod
    def aware_pickup(cls, value):
        if value and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('領匙時間必須包含時區')
        return value.astimezone(HK) if value else None


def now_hk():
    return datetime.now(HK)


def mode():
    return os.getenv('CALENDAR_MODE', 'mock').lower()


def calendar_id():
    return os.getenv('GOOGLE_CALENDAR_ID', 'lemma@supermama.homes')


def safe_config():
    return {
        'mode': mode(), 'calendar_id': calendar_id(), 'timezone': 'Asia/Hong_Kong',
        'credentials_configured': bool(os.getenv('GOOGLE_APPLICATION_CREDENTIALS') or os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')),
        'read_only': True, 'starts': list(STARTS),
        'status': '未驗證連線' if mode() == 'google' else '固定測試資料',
    }


def access_token():
    # Lazy imports keep mock-only installations usable during an update.
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    try:
        raw = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
        path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        if raw:
            info = json.loads(raw)
        elif path:
            info = json.loads(Path(path).read_text())
        else:
            raise CalendarError('未配置Google憑證；請設定GOOGLE_APPLICATION_CREDENTIALS或GOOGLE_SERVICE_ACCOUNT_JSON。')
        # Do not allow a credential file to redirect the signing assertion elsewhere.
        if info.get('type') != 'service_account' or info.get('token_uri') != 'https://oauth2.googleapis.com/token':
            raise CalendarError('Google憑證格式不正確，請使用官方Service Account JSON。')
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        transport = Request()
        def bounded_request(*args, **kwargs):
            kwargs['timeout'] = 15
            return transport(*args, **kwargs)
        credentials.refresh(bounded_request)
        return credentials.token
    except CalendarError:
        raise
    except Exception:
        raise CalendarError('Google憑證載入或授權失敗；請檢查檔案、金鑰及網絡，勿貼出私鑰。') from None


def parse_datetime(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Missing timezone')
    return parsed.astimezone(HK)


async def fetch_busy(start, end):
    if mode() != 'google':
        raise CalendarError('尚未啟用Google日曆模式；固定測試資料不能當作真實空位。')
    try:
        token = await asyncio.wait_for(asyncio.to_thread(access_token), timeout=20)
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                'https://www.googleapis.com/calendar/v3/freeBusy',
                headers={'Authorization': 'Bearer ' + token},
                json={'timeMin': start.isoformat(), 'timeMax': end.isoformat(),
                      'timeZone': 'Asia/Hong_Kong', 'items': [{'id': calendar_id()}]},
            )
        audit.record('calendar_http',{'status':response.status_code})
        if response.status_code != 200:
            code = response.status_code
            raise CalendarError(f'Google日曆查詢失敗（HTTP {code}）；請檢查Calendar API、分享權限、配額及網絡。')
        body = response.json()
        entry = body.get('calendars', {}).get(calendar_id())
        if not isinstance(entry, dict) or entry.get('errors') or not isinstance(entry.get('busy'), list):
            raise CalendarError('Google未回傳完整日曆資料；請確認指定日曆已分享給Service Account。')
        busy = []
        for item in entry['busy']:
            a, b = parse_datetime(item['start']), parse_datetime(item['end'])
            if b <= a:
                raise ValueError('Invalid busy interval')
            busy.append((a, b))
        return busy
    except CalendarError:
        raise
    except Exception:
        raise CalendarError('Google日曆回應不完整或連線失敗；未提供任何替代空位。') from None


def conflicts(start, end, intervals):
    # Google busy end is exclusive: touching boundaries alone do not overlap.
    return any(start < b and end > a for a, b in intervals)


def meal_breaks(day):
    intervals = []
    value = os.getenv('CALENDAR_MEAL_BREAKS', '').strip()
    for interval in value.split(',') if value else []:
        a, b = interval.strip().split('-')
        start = datetime.combine(day, time.fromisoformat(a), HK)
        end = datetime.combine(day, time.fromisoformat(b), HK)
        if not 30 <= (end - start).total_seconds()/60 <= 50:
            raise ValueError('Meal duration must be 30–50 minutes')
        intervals.append((start, end))
    if value and len(intervals) != 2:
        raise ValueError('Configure both lunch and dinner')
    return intervals


def travel_minutes():
    value = os.getenv('CALENDAR_TRAVEL_MINUTES', '').strip()
    if not value:
        return None
    minutes = int(value)
    if not 0 <= minutes <= 180:
        raise ValueError('Invalid travel buffer')
    return minutes


def business_calendar():
    data = json.loads(Path(__file__).with_name('hk_holidays.json').read_text())
    value = os.getenv('CALENDAR_BUSINESS_WEEK', 'mon_sat').strip()
    if not value: value = 'mon_sat'
    if value not in ('', 'mon_fri', 'mon_sat'):
        raise CalendarError('CALENDAR_BUSINESS_WEEK只可為mon_fri或mon_sat。')
    return (6 if value == 'mon_sat' else 5), {date.fromisoformat(d) for d in data['dates']}, set(data['covered_years']), not bool(value)


def completion_window(completion):
    count, holidays, years, provisional = business_calendar()
    def workday(day):
        if day.year not in years:
            raise CalendarError('公眾假期資料未涵蓋所需年份，請人工確認領匙日期。')
        return day.weekday() < count and day not in holidays
    def advance(day, n):
        for _ in range(n):
            day += timedelta(days=1)
            while not workday(day): day += timedelta(days=1)
        return day
    letter = advance(completion, 2)
    return {'letter_date':letter.isoformat(),'earliest_pickup':advance(letter,2).isoformat(),
            'pickup_deadline':advance(completion,10).isoformat(),
            'assumption':('暫按' if provisional else '按已配置')+('星期一至六' if count==6 else '星期一至五')+'、排除香港公眾假期；成交翌日起算第一個工作天。',
            'provisional':provisional, 'confirmed':False}, workday


async def lookup(request: AvailabilityRequest):
    audit.record('query',request.model_dump(mode='json'))
    current = now_hk()
    if request.start_date < current.date() or request.start_date > current.date() + timedelta(days=366):
        raise CalendarError('請選擇今天起一年內的查詢開始日期。')
    planning = None
    if request.completion_date and not request.pickup_at and request.plan_from_completion:
        planning, workday = completion_window(request.completion_date)
        audit.record('planning',planning)
        first = max(current.date(), date.fromisoformat(planning['earliest_pickup']), request.start_date)
        deadline = date.fromisoformat(planning['pickup_deadline'])
        if first > deadline:
            return {'status':'needs_details','source':'rules','slots':[], 'requires_review':True,
                    'summary':'估算領匙期限已過，請先同律師樓及管理處確認可領匙日期。',
                    'notes':[planning['assumption']], 'customer_question':'按目前估算，你嘅領匙期限可能已過。想先確認你係咪已經領匙，或者管理處有冇另行安排？'}
        request = request.model_copy(update={'start_date':first,'days':min(request.days,14,(deadline-first).days+3)})
    if request.completion_date and not request.pickup_at and not planning:
        return {
            'status': 'needs_details', 'source': 'rules', 'slots': [], 'requires_review': True,
            'summary': '只有成交日期，請先確認實際領匙日期及時間，再查驗樓空位。',
            'notes': ['成交後最快第二個工作天可領律師信，再致電管理處；一般可約兩個工作天後領匙。',
                      '成交日起計10個工作天內或之前可領匙；星期六、假期及起算方式未定案，不自動計算最後領匙日。',
                      '星期日不建議領匙；優先安排領匙當日或之後週末驗樓。'],
        }
    if request.pickup_at and request.completion_date and request.pickup_at.date() < request.completion_date:
        return {'status': 'needs_details', 'source': 'rules', 'slots': [], 'requires_review': True,
                'summary': '領匙時間早於成交日期，請先確認兩個日期，未查詢日曆。', 'notes': []}
    if request.pickup_at and request.pickup_at.weekday() == 6:
        return {'status': 'needs_details', 'source': 'rules', 'slots': [], 'requires_review': True,
                'summary': '領匙日期為星期日，與目前管理處規則不符，請先人工確認領匙安排。', 'notes': []}
    start = datetime.combine(request.start_date, time.min, HK)
    end = start + timedelta(days=request.days)
    try:
        travel = travel_minutes()
        for offset in range(request.days):
            meal_breaks(request.start_date + timedelta(days=offset))
    except (ValueError, TypeError):
        raise CalendarError('用餐／交通設定不正確；用餐需兩段各30–50分鐘，交通需0–180分鐘。') from None
    # Include adjacent days so busy intervals just outside the range retain buffers.
    audit.record('calendar_request',{'calendar_id':calendar_id(),'timezone':'Asia/Hong_Kong',
        'timeMin':start-timedelta(days=1),'timeMax':end+timedelta(days=1),
        'effective_query':request.model_dump(mode='json')})
    busy = await fetch_busy(start - timedelta(days=1), end + timedelta(days=1))
    audit.record('busy',{'calendar_id':calendar_id(),'intervals':[{'start':a,'end':b} for a,b in busy]})
    padded = [(a-timedelta(minutes=travel or 0), b+timedelta(minutes=travel or 0)) for a,b in busy]
    notes = ['只查日曆忙碌區間，沒有建立或保留預約；多人容量及當日完整行程需人工確認。',
             '候選時段是替代選項，並非可同時全部安排；重疊場次必須轉人工。']
    if planning:
        notes.extend([planning['assumption'],'律師信、領匙最早日及10工作天期限均屬估算；未核實管理處可約日期、辦公時間或已領信。'])
    if travel is None:
        notes.append('交通時間未設定，候選時段仍需核實交通。')
    if not os.getenv('CALENDAR_MEAL_BREAKS', '').strip():
        notes.append('午晚飯尚未配置，須另預留各30–50分鐘，不能直接排滿。')
    if not request.pickup_at:
        notes.append('未提供領匙時間，無法核對領匙後1小時規則。')
    audit.record('policy',{'starts':STARTS,'duration_minutes':request.duration_minutes,
        'preference':request.preference,'weekend_only':request.weekend_only,
        'weekday_evening':request.include_weekday_evening,'travel_minutes':travel,
        'meal_breaks':os.getenv('CALENDAR_MEAL_BREAKS',''),'pickup_buffer_minutes':60})
    slots = []
    decisions = []
    for offset in range(request.days):
        day = request.start_date + timedelta(days=offset)
        for hhmm in STARTS:
            a = datetime.combine(day, time.fromisoformat(hhmm), HK)
            b = a + timedelta(minutes=request.duration_minutes)
            decision={'start':a.isoformat(),'end':b.isoformat(),'reason':'available'}
            decisions.append(decision)
            if request.weekend_only and day.weekday() < 5:
                decision['reason']='weekend_only';continue
            if hhmm == '18:00' and day.weekday() < 5 and not request.include_weekday_evening:
                decision['reason']='weekday_evening'
                continue
            if request.preference == 'morning' and hhmm != '09:00':
                decision['reason']='preference'
                continue
            if request.preference == 'afternoon' and hhmm == '09:00':
                decision['reason']='preference'
                continue
            if request.preference == 'evening' and hhmm != '18:00':
                decision['reason']='preference';continue
            if a <= current:
                decision['reason']='past';continue
            if request.pickup_at and a < request.pickup_at + timedelta(hours=1):
                decision['reason']='pickup_buffer'
                continue
            if conflicts(a, b, padded):
                decision['reason']='busy'
                decision['conflicts']=[{'start':x.isoformat(),'end':y.isoformat()} for x,y in padded if a<y and b>x]
                continue
            if conflicts(a, b, meal_breaks(day)):
                decision['reason']='meal'
                continue
            pickup_plan = None
            if planning:
                pickup_day = min(day, date.fromisoformat(planning['pickup_deadline']))
                # A morning inspection cannot assume management opens at 08:00.
                if pickup_day == day and a.hour < 12: pickup_day -= timedelta(days=1)
                while not workday(pickup_day): pickup_day -= timedelta(days=1)
                if pickup_day < date.fromisoformat(planning['earliest_pickup']) or pickup_day < current.date():
                    decision['reason']='pickup_window';continue
                pickup_plan = {'date':pickup_day.isoformat(),
                               'latest_time':(a-timedelta(hours=1)).strftime('%H:%M') if pickup_day==day else None,
                               'same_day':pickup_day==day,'confirmed':False}
            same_day = bool(request.pickup_at and day == request.pickup_at.date())
            preferred = same_day or day.weekday() >= 5
            slots.append({'start': a.isoformat(), 'end': b.isoformat(), 'preferred': preferred,
                          'reason': '領匙當日' if same_day else '週末' if day.weekday() >= 5 else '其他日期',
                          'evening': hhmm == '18:00', 'requires_review': True,
                          **({'pickup_suggestion':pickup_plan} if pickup_plan else {})})
    slots.sort(key=lambda s: (False if planning else not s['preferred'], s['start']))
    audit.record('candidates',decisions)
    return {'status': 'candidates' if slots else 'no_slots', 'source': 'google',
            'calendar_id': calendar_id(), 'timezone': 'Asia/Hong_Kong',
            'checked_at': current.isoformat(), 'duration_minutes': request.duration_minutes,
            'slots': slots[:12], 'total_candidates': len(slots), 'requires_review': True,
            'summary': '已查Google日曆；以下為日曆無衝突候選，仍需人工核實。' if slots else '查詢範圍內沒有符合條件的候選時段，請改日期或轉人工。',
            'notes': notes, 'planning':planning, 'query':request.model_dump(mode='json')}


def render_customer_reply(result):
    """Customer copy uses only verified slots; operational notes stay in the UI."""
    if result['status'] == 'needs_details':
        if result.get('customer_question'): return result['customer_question']
        if '領匙日期為星期日' in result['summary']:
            return '你提到星期日領匙，不過按目前安排管理處星期日唔開門。想先同你確認實際領匙日期同時間，再幫你查驗樓時段。'
        if '領匙時間早於成交日期' in result['summary']:
            return '你提供嘅領匙時間早過成交日期，想先確認兩個日期有冇寫錯，再幫你查驗樓時段。'
        if result.get('source') == 'rules' and '只有成交日期' in result['summary']:
            return ('收到！成交後最快第二個工作天可以拎律師信，再打電話去管理處約領匙，一般可約兩個工作天後。'
                    '\n成交日起計10個工作天內或之前可以領匙，實際日期要同律師樓及管理處確認。'
                    '\n你約咗邊日、幾點領匙呀？確認後我再幫你查驗樓時間，會優先睇領匙當日或週末。')
        return '請先確認領匙日期、時間，同埋想邊日驗樓呀？有齊資料，我再幫你查合適時段。'
    if not result['slots']:
        return '幫你查過喇，今次查詢範圍暫時未搵到合適嘅驗樓時段。你仲有邊幾日方便？我再幫你睇吓。'
    planning = result.get('planning')
    if planning:
        lines = ['我先按驗樓空位幫你配合領匙安排，以下方案都要先同律師樓及管理處確認：',
                 planning['assumption'], '最快領信及管理處兩個工作天後可約領匙都係估算，唔代表已經約到。']
    else:
        lines = ['幫你查過喇，可以先考慮以下驗樓時段：']
        query = result.get('query',{})
        if query.get('pickup_at'):
            pickup = parse_datetime(query['pickup_at'])
            same_day = any(parse_datetime(s['start']).date()==pickup.date() for s in result['slots'])
            lines.insert(0, f'你{pickup.month}月{pickup.day}日{pickup:%H:%M}領匙，驗樓最早可以安排喺領匙一小時後。')
            if not same_day: lines.insert(1,'今次查詢範圍內未見領匙當日合適場次，以下係其他候選。')
    offered = result['slots'][:3]
    if planning:
        first = result['slots'][0]
        alternative = next((s for s in result['slots'][1:] if parse_datetime(s['start']).weekday()>=5
                            and s['start'][:10]!=first['start'][:10]),None)
        offered = [first,alternative] if alternative else result['slots'][:2]
    for slot in offered:
        start, end = parse_datetime(slot['start']), parse_datetime(slot['end'])
        weekday = '一二三四五六日'[start.weekday()]
        lines.append(f'・{start.month}月{start.day}日（星期{weekday}）{start:%H:%M}–{end:%H:%M}')
        if planning and slot.get('pickup_suggestion'):
            p = slot['pickup_suggestion']
            day = date.fromisoformat(p['date'])
            if p['same_day']:
                lines.append(f"  可向管理處查詢{day.month}月{day.day}日領匙，最遲{p['latest_time']}完成，預留至少一小時。")
            else:
                lines.append(f'  可向管理處查詢{day.month}月{day.day}日先領匙，再到上述時段驗樓。')
    lines.extend(['你比較方便邊個時段呀？我哋再核實安排，確認後先落實預約。'])
    return '\n'.join(lines)


def render_result(result):
    lines = [result['summary']]
    for index, slot in enumerate(result['slots'], 1):
        start, end = parse_datetime(slot['start']), parse_datetime(slot['end'])
        day_name = '一二三四五六日'[start.weekday()]
        suffix = '；晚場，平日非優先' if slot['evening'] and start.weekday() < 5 else '；晚場' if slot['evening'] else ''
        lines.append(f"{index}. {start:%Y-%m-%d} 星期{day_name} {start:%H:%M}–{end:%H:%M}（{slot['reason']}{suffix}）")
    lines.extend(result['notes'])
    lines.append('【只供查詢建議，未建立預約；尚未查驗登記紀錄】')
    return '\n'.join(lines)
