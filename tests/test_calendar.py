import asyncio
import json
import sqlite3
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend import calendar_service as cal
from backend import app as m

FIXED = datetime(2026, 11, 3, 8, tzinfo=cal.HK)  # Tuesday


def dt(value):
    return cal.parse_datetime(value + '+08:00')


def query(**kw):
    return cal.AvailabilityRequest(start_date='2026-11-03', days=1, **kw)


@pytest.fixture(autouse=True)
def calendar_env(monkeypatch):
    monkeypatch.setattr(cal, 'now_hk', lambda: FIXED)
    monkeypatch.setenv('CALENDAR_MODE', 'google')
    for name in ('CALENDAR_MEAL_BREAKS', 'CALENDAR_TRAVEL_MINUTES', 'GOOGLE_SERVICE_ACCOUNT_JSON', 'GOOGLE_APPLICATION_CREDENTIALS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('GOOGLE_CALENDAR_ID', 'lemma@supermama.homes')


@pytest.fixture
def busy(monkeypatch):
    intervals = []
    async def fetch(start, end):
        assert start.tzinfo and end.tzinfo
        return intervals
    monkeypatch.setattr(cal, 'fetch_busy', fetch)
    return intervals


def result(**kw):
    return asyncio.run(cal.lookup(query(**kw)))


def starts(data):
    return [s['start'][11:16] for s in data['slots']]


def test_four_starts_weekday_evening_is_opt_in(busy):
    assert starts(result()) == ['09:00', '12:30', '15:45']
    data = result(include_weekday_evening=True)
    assert starts(data) == ['09:00', '12:30', '15:45', '18:00']
    assert data['slots'][-1]['end'][11:16] == '21:00'
    assert all(s['requires_review'] for s in data['slots'])


def test_pickup_plus_one_hour_and_weekend_preference(busy):
    data = result(pickup_at='2026-11-03T11:30:00+08:00')
    assert starts(data) == ['12:30', '15:45']  # exact 1h is eligible
    data = asyncio.run(cal.lookup(cal.AvailabilityRequest(
        start_date='2026-11-03', days=7, pickup_at='2026-11-03T11:30:00+08:00')))
    assert data['slots'][0]['reason'] == '領匙當日'
    assert any(s['reason'] == '週末' and s['evening'] for s in data['slots'])
    priorities = [s['preferred'] for s in data['slots']]
    assert priorities == sorted(priorities, reverse=True)


def test_late_pickup_not_same_day(busy):
    assert not result(pickup_at='2026-11-03T18:00:00+08:00', include_weekday_evening=True)['slots']


def test_busy_overlap_and_exclusive_boundary(busy):
    busy.append((dt('2026-11-03T09:00:00'), dt('2026-11-03T12:30:00')))
    assert starts(result()) == ['12:30', '15:45']
    busy.append((dt('2026-11-03T15:00:00'), dt('2026-11-03T16:00:00')))
    assert not result()['slots']


def test_full_busy_day_has_no_slots(busy):
    busy.append((dt('2026-11-03T00:00:00'), dt('2026-11-04T00:00:00')))
    data = result(include_weekday_evening=True)
    assert data['status'] == 'no_slots' and not data['slots']
    assert '上午10點' not in cal.render_result(data)


def test_travel_and_meals_filter_slots(busy, monkeypatch):
    busy.append((dt('2026-11-03T08:00:00'), dt('2026-11-03T08:45:00')))
    monkeypatch.setenv('CALENDAR_TRAVEL_MINUTES', '30')
    monkeypatch.setenv('CALENDAR_MEAL_BREAKS', '12:00-12:40,18:30-19:10')
    assert not result()['slots']  # 09:00 travel, 12:30 lunch, 15:45 dinner


@pytest.mark.parametrize('name,value', [('CALENDAR_TRAVEL_MINUTES','-1'), ('CALENDAR_TRAVEL_MINUTES','oops'),
    ('CALENDAR_MEAL_BREAKS','12:00-12:15,19:00-19:30'), ('CALENDAR_MEAL_BREAKS','12:00-12:30')])
def test_invalid_operational_config_fails_closed(busy, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(cal.CalendarError):
        result()


def test_unknown_meals_travel_and_pickup_are_not_confirmed(busy):
    data = result()
    assert data['requires_review']
    assert any('交通時間未設定' in n for n in data['notes'])
    assert any('午晚飯尚未配置' in n for n in data['notes'])
    assert any('未提供領匙' in n for n in data['notes'])


def test_completion_only_does_not_guess_business_days(monkeypatch):
    async def forbidden(*args):
        pytest.fail('Must not query calendar before pickup clarification')
    monkeypatch.setattr(cal, 'fetch_busy', forbidden)
    data = result(completion_date='2026-11-05')
    assert data['status'] == 'needs_details' and data['source'] == 'rules'
    assert '10個工作天' in cal.render_result(data)


def test_sunday_pickup_requires_clarification(busy):
    assert result(pickup_at='2026-11-08T09:00:00+08:00')['status'] == 'needs_details'


def test_input_bounds_and_timezone():
    with pytest.raises(ValidationError): query(pickup_at='2026-11-03T10:00:00')
    with pytest.raises(ValidationError): query(duration_minutes=105)
    with pytest.raises(ValidationError): cal.AvailabilityRequest(start_date='2026-11-03', days=30)
    assert query(pickup_at='2026-11-03T03:00:00Z').pickup_at.hour == 11


def test_range_does_not_offer_past_or_unbounded_dates(busy):
    for date in ('2026-11-02', '2028-01-01'):
        with pytest.raises(cal.CalendarError):
            asyncio.run(cal.lookup(cal.AvailabilityRequest(start_date=date)))


def test_duration_and_preference(busy):
    data = result(duration_minutes=150, preference='afternoon')
    assert starts(data) == ['12:30', '15:45']
    assert data['slots'][0]['end'][11:16] == '15:00'
    assert starts(result(preference='morning')) == ['09:00']


def fake_google(monkeypatch, response):
    seen = []
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            seen.append((url, kwargs))
            return response
    monkeypatch.setattr(cal, 'access_token', lambda: 'fake-only')
    monkeypatch.setattr(cal.httpx, 'AsyncClient', FakeClient)
    return seen


def test_freebusy_reads_only_specified_calendar(monkeypatch):
    seen = fake_google(monkeypatch, httpx.Response(200, json={'calendars': {
        'lemma@supermama.homes': {'busy': [{'start':'2026-11-03T01:00:00Z','end':'2026-11-03T04:00:00Z'}]}}}))
    data = result()
    assert '09:00' not in starts(data)
    assert seen[0][0] == 'https://www.googleapis.com/calendar/v3/freeBusy'
    assert seen[0][1]['json']['items'] == [{'id':'lemma@supermama.homes'}]
    assert 'fake-only' not in str(data)


@pytest.mark.parametrize('status,body', [(403, {'secret':'DO-NOT-LOG'}), (200, {}),
    (200, {'calendars':{'lemma@supermama.homes':{'errors':[{'reason':'notFound'}]}}}),
    (200, {'calendars':{'lemma@supermama.homes':{}}}),
    (200, {'calendars':{'lemma@supermama.homes':{'busy':[{'start':'bad','end':'bad'}]}}})])
def test_failed_or_partial_google_never_becomes_free(monkeypatch, status, body):
    fake_google(monkeypatch, httpx.Response(status, json=body))
    with pytest.raises(cal.CalendarError) as exc:
        result()
    assert 'DO-NOT-LOG' not in str(exc.value)


def test_mock_mode_does_not_claim_google():
    import os
    os.environ['CALENDAR_MODE']='mock'
    try:
        with pytest.raises(cal.CalendarError): result()
    finally:
        os.environ['CALENDAR_MODE']='google'


def test_credentials_missing_and_invalid_never_print_key(monkeypatch, tmp_path):
    with pytest.raises(cal.CalendarError): cal.access_token()
    monkeypatch.setenv('GOOGLE_SERVICE_ACCOUNT_JSON', '{"private_key":"SECRET-PRIVATE-KEY"}')
    with pytest.raises(cal.CalendarError) as exc: cal.access_token()
    assert 'SECRET-PRIVATE-KEY' not in str(exc.value)
    # Neither Google tokens nor credential paths are exposed by the config summary.
    assert 'SECRET-PRIVATE-KEY' not in json.dumps(cal.safe_config())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'DB', str(tmp_path/'jobs.db'))
    monkeypatch.setattr(m, 'ADMIN_TOKEN', 'test-admin')
    monkeypatch.setenv('AI_PROVIDER', 'mock')
    monkeypatch.setenv('LIVE_SEND_ENABLED', 'false')
    monkeypatch.setenv('EMBEDDED_WORKER', 'false')
    with TestClient(m.app) as c:
        c.headers['Authorization']='Bearer test-admin'
        yield c


def test_calendar_endpoint_is_admin_only(client, busy):
    body = query().model_dump(mode='json')
    assert client.post('/api/calendar/availability', json=body, headers={'Authorization':''}).status_code == 401
    r = client.post('/api/calendar/availability', json=body)
    assert r.status_code == 200 and r.json()['source'] == 'google'
    assert client.post('/api/calendar/availability', json={'start_date':'garbage'}).status_code == 422


def test_calendar_failure_is_safely_reported(client, monkeypatch):
    async def broken(*args): raise cal.CalendarError('Google日曆未授權')
    monkeypatch.setattr(cal, 'fetch_busy', broken)
    response=client.post('/api/calendar/availability', json=query().model_dump(mode='json'))
    assert response.status_code == 503
    assert response.json()['detail'] == 'Google日曆未授權'


def add(client, schedule=True):
    body={'message_id':'calendar-job','message':'驗樓，請用確認後的日期','contact_id':'test-001'}
    if schedule: body['schedule']=query(pickup_at='2026-11-03T11:00:00+08:00').model_dump(mode='json')
    r=client.post('/api/test/messages',json=body)
    assert r.status_code==200, r.text
    return body


def test_worker_uses_real_query_and_persists_result(client, busy):
    body=add(client)
    asyncio.run(m.process_one())
    j=client.get('/api/jobs').json()['jobs'][0]
    assert j['calendar_result']['source']=='google' and j['status']=='simulated'
    assert j['provider']=='rules' and '12:30' in j['reply']
    assert '上午10點' not in j['reply']
    assert client.post('/api/test/messages',json=body).json()['duplicate']
    body['schedule']['duration_minutes']=120
    assert client.post('/api/test/messages',json=body).status_code==409


def test_google_mode_no_conditions_asks_instead_of_guessing(client, busy):
    add(client, schedule=False)
    asyncio.run(m.process_one())
    j=client.get('/api/jobs').json()['jobs'][0]
    assert j['calendar_result']['status']=='needs_details'
    assert '請先' in j['reply'] and '上午10點' not in j['reply']


def test_calendar_failure_in_worker_no_outbox(client, monkeypatch):
    async def broken(*args): raise cal.CalendarError('Google日曆未授權')
    monkeypatch.setattr(cal, 'fetch_busy', broken)
    add(client); asyncio.run(m.process_one())
    j=client.get('/api/jobs').json()['jobs'][0]
    assert j['status']=='failed' and not j['reply']
    with m.db() as db: assert db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0


def test_calendar_test_job_never_sends_even_with_live_flag(client, busy, monkeypatch):
    monkeypatch.setenv('LIVE_SEND_ENABLED', 'true')
    def forbidden(*args, **kwargs):
        raise AssertionError('A calendar test must not call the send adapter')
    monkeypatch.setattr(m.httpx, 'AsyncClient', forbidden)
    add(client); asyncio.run(m.process_one())
    job = client.get('/api/jobs').json()['jobs'][0]
    assert job['status'] == 'simulated'
    with m.db() as db:
        assert db.execute('SELECT state FROM outbox').fetchone()[0] == 'simulated'


def test_deepseek_cannot_inject_slots(client, busy, monkeypatch):
    monkeypatch.setenv('AI_PROVIDER','deepseek')
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    class Model:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            return httpx.Response(200, json={'choices':[{'message':{'content':'{"greeting_id":2,"slots":["1999-01-01 10:00"],"reply":"已付款，已預約"}'}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(m.httpx,'AsyncClient',Model)
    add(client); asyncio.run(m.process_one())
    j=client.get('/api/jobs').json()['jobs'][0]
    assert j['provider']=='deepseek' and j['status']=='simulated'
    assert '1999-01-01' not in j['reply'] and '已付款' not in j['reply']
    assert '12:30' in j['reply']


def test_additive_migration_preserves_old_jobs(tmp_path, monkeypatch):
    path=tmp_path/'old.db'; monkeypatch.setattr(m,'DB',str(path))
    # Build the exact original schema by omitting only the migration statements.
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, contact_id TEXT, message TEXT, channel TEXT, created REAL, updated REAL, status TEXT, stage INTEGER, attempts INTEGER, reply TEXT, intent TEXT, error TEXT, lease_until REAL, claim TEXT, fail_once INTEGER, mode TEXT, provider TEXT)')
        db.execute("INSERT INTO jobs(id,status) VALUES('old-record','simulated')")
    m.init_db(); m.init_db()
    with m.db() as db:
        row=db.execute("SELECT id,status,schedule,calendar_result FROM jobs WHERE id='old-record'").fetchone()
        assert tuple(row)==('old-record','simulated',None,None)


def test_pickup_before_completion_requires_clarification(busy):
    data=result(pickup_at='2026-11-03T10:00:00+08:00',completion_date='2026-11-05')
    assert data['status']=='needs_details' and not data['slots']


def test_concurrent_startup_migration(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(m,'DB',str(tmp_path/'concurrent.db'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:m.init_db(),range(2)))
    with m.db() as db:
        names=[r['name'] for r in db.execute('PRAGMA table_info(jobs)')]
        assert names.count('schedule')==names.count('calendar_result')==1
