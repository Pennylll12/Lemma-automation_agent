import asyncio
import json
from datetime import date, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from backend import app as m, calendar_service as cal, intent_service as intent

ANCHOR=date(2026,11,3)
NOW=datetime(2026,11,3,10,tzinfo=cal.HK)

@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER','mock')
    monkeypatch.setenv('CALENDAR_MODE','google')
    monkeypatch.setenv('CALENDAR_BUSINESS_WEEK','mon_sat')
    monkeypatch.setenv('LIVE_SEND_ENABLED','false')
    monkeypatch.setenv('EMBEDDED_WORKER','false')
    monkeypatch.delenv('CALENDAR_MEAL_BREAKS',raising=False)
    monkeypatch.delenv('CALENDAR_TRAVEL_MINUTES',raising=False)
    monkeypatch.setattr(cal,'now_hk',lambda:NOW)
    monkeypatch.setattr(m.time,'time',lambda:NOW.timestamp())


def understand(text, context=None):
    return asyncio.run(intent.interpret(text,ANCHOR,context))

@pytest.mark.parametrize('phrase,expected',[
    ('11月5日','2026-11-05'),('2027年1月2日','2027-01-02'),('2026/11/05','2026-11-05'),
    ('5/11','2026-11-05'),('下星期二','2026-11-10'),('星期六','2026-11-07'),
    ('明天','2026-11-04'),('後日','2026-11-05'),('週末','2026-11-07'),('下個月5號','2026-12-05')])
def test_date_resolution(phrase,expected):
    assert intent.resolve_date(phrase,ANCHOR).isoformat()==expected

@pytest.mark.parametrize('text',[
    '我11月5日上午11點攞鎖匙，想同日驗樓',
    '我11月5日上午十一點領匙，想同日驗樓',
    '我11月5日11:00取鑰匙，想同日驗樓'])
def test_pickup_and_same_day(text):
    data=understand(text)
    assert data['status']=='ready',data
    q=data['schedule']
    assert q['start_date']=='2026-11-05' and q['days']==1
    assert q['pickup_at']=='2026-11-05T11:00:00+08:00'
    assert q['preference']=='any' and q['duration_minutes']==180


def test_completion_detected_and_planning_enabled():
    data=understand('我11月5日成交，應該幾時攞鎖匙同驗樓？')
    assert data['intent']=='completion_advice' and not data['questions']
    assert data['schedule']['completion_date']=='2026-11-05'
    assert data['schedule']['plan_from_completion']
    assert data['schedule']['pickup_at'] is None


def test_pickup_hour_ambiguity_asks_without_query():
    data=understand('我11月5日11點攞鎖匙，想驗樓')
    assert data['status']=='needs_details' and not data['schedule']


def test_pickup_date_without_hour_asks():
    data=understand('我11月5日攞鎖匙，想同日驗樓')
    assert not data['schedule'] and '幾點' in data['questions'][0]


def test_requested_sunday_inspection_is_allowed():
    data=understand('我11月7日上午11點攞鎖匙，想11月8日驗樓')
    assert data['schedule']['start_date']=='2026-11-08'
    assert data['schedule']['days']==1


def test_weekend_and_afternoon_constraints():
    q=understand('想約週末下午驗樓')['schedule']
    assert q['start_date']=='2026-11-07' and q['days']==2 and q['weekend_only']
    assert q['preference']=='afternoon'


def test_evening_does_not_offer_morning():
    q=understand('想約11月5日晚上驗樓')['schedule']
    assert q['preference']=='evening' and q['include_weekday_evening']


def test_contradictory_weekday_and_corrections_need_clarification():
    assert not understand('想約11月5日星期六驗樓')['schedule']
    assert not understand('唔係11月5日，改做11月6日驗樓')['schedule']


def test_same_contact_followup_and_new_pickup_replaces_old():
    first=understand('我11月5日上午11點攞鎖匙，想同日驗樓')
    follow=understand('咁下午有冇其他時間？',first)
    assert follow['schedule']['pickup_at']==first['schedule']['pickup_at']
    assert follow['schedule']['preference']=='afternoon'
    updated=understand('我11月6日下午3點攞鎖匙',first)
    assert updated['schedule']['pickup_at']=='2026-11-06T15:00:00+08:00'
    assert updated['schedule']['start_date']=='2026-11-06'


@pytest.mark.parametrize('text,expected',[('我想取消預約','cancel'),('我想改期到11月8日','reschedule'),('冷氣點訂？','other')])
def test_no_calendar_for_other_intents(text,expected):
    data=understand(text)
    assert data['intent']==expected and data['schedule'] is None
    assert '查過' not in intent.clarification(data)


def test_model_cannot_invent_a_date(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER','deepseek');monkeypatch.setenv('DEEPSEEK_API_KEY','fake-only')
    class Model:
        def __init__(self,**kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            assert 'fake-only' not in kwargs['json']['messages'][0]['content']
            return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':json.dumps({'intent':'availability','inspection_date':'2099年1月1日'})}}]})
    monkeypatch.setattr(intent.httpx,'AsyncClient',Model)
    data=understand('想約11月5日驗樓')
    assert data['provider']=='rules' and data['schedule']['start_date']=='2026-11-05'
    assert data['notes']


@pytest.fixture
def busy(monkeypatch):
    intervals=[]
    async def fetch(start,end):return intervals
    monkeypatch.setattr(cal,'fetch_busy',fetch)
    return intervals


def test_completion_business_days_and_real_calendar_pairing(busy):
    # Thu 5 closing -> Fri 6, Sat 7 letter -> Mon 9, Tue 10 pickup.
    info,_=cal.completion_window(date(2026,11,5))
    assert info['letter_date']=='2026-11-07'
    assert info['earliest_pickup']=='2026-11-10'
    assert info['pickup_deadline']=='2026-11-17'
    q=cal.AvailabilityRequest.model_validate(understand('我11月5日成交，幾時驗樓？')['schedule'])
    result=asyncio.run(cal.lookup(q))
    assert result['source']=='google' and result['planning']
    assert result['slots'][0]['start'].startswith('2026-11-10T12:30')
    assert all(s['pickup_suggestion']['date']>='2026-11-10' for s in result['slots'])
    assert all(date.fromisoformat(s['pickup_suggestion']['date']).weekday()!=6 for s in result['slots'])
    reply=cal.render_customer_reply(result)
    assert '領匙' in reply and '11:30' in reply and '估算' in reply


def test_holidays_are_excluded_and_unknown_year_rejected():
    info,_=cal.completion_window(date(2026,12,24))
    assert info['letter_date']=='2026-12-29'  # Fri/Sat holidays, Sunday excluded.
    with pytest.raises(cal.CalendarError):cal.completion_window(date(2028,11,5))


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'DB',str(tmp_path/'jobs.db'))
    monkeypatch.setattr(m,'ADMIN_TOKEN','test-admin')
    with TestClient(m.app) as client:
        client.headers['Authorization']='Bearer test-admin'
        yield client


def test_interpret_endpoint_requires_admin_and_does_not_enqueue(client):
    body={'message':'我11月5日上午11點領匙，想同日驗樓'}
    assert client.post('/api/intent/interpret',json=body,headers={'Authorization':''}).status_code==401
    response=client.post('/api/intent/interpret',json=body)
    assert response.status_code==200 and response.json()['schedule']
    assert not client.get('/api/jobs').json()['jobs']


def add(client,text,**kwargs):
    response=client.post('/api/test/messages',json={'message':text,**kwargs})
    assert response.status_code==200,response.text
    asyncio.run(m.process_one())
    return client.get('/api/jobs').json()['jobs'][0]


def test_worker_auto_identifies_queries_and_retains_immutable_payload(client,busy):
    job=add(client,'我11月5日上午11點領匙，想同日驗樓',message_id='auto1')
    assert job['status']=='simulated' and job['calendar_result']['source']=='google'
    assert job['schedule'] is None and job['resolved_schedule']['start_date']=='2026-11-05'
    assert job['understanding']['intent']=='availability'
    duplicate=client.post('/api/test/messages',json={'message':job['message'],'message_id':'auto1'})
    assert duplicate.json()['duplicate']


def test_context_is_contact_scoped_and_resettable(client,busy):
    add(client,'我11月5日上午11點領匙，想同日驗樓')
    same=add(client,'咁下午有冇其他時間？')
    assert same['resolved_schedule']['pickup_at'].startswith('2026-11-05T11:00')
    other=add(client,'咁下午有冇其他時間？',contact_id='test-002')
    assert other['resolved_schedule'] is None
    reset=add(client,'咁下午有冇其他時間？',reset_context=True)
    assert reset['resolved_schedule'] is None


def test_manual_schedule_wins_over_text(client,busy):
    q=cal.AvailabilityRequest(start_date='2026-11-06',days=1).model_dump(mode='json')
    job=add(client,'我想約11月5日驗樓',schedule=q)
    assert job['resolved_schedule']['start_date']=='2026-11-06'
    assert job['understanding'] is None


def test_other_intent_never_queries_calendar(client,monkeypatch):
    async def forbidden(*args):raise AssertionError('No calendar call expected')
    monkeypatch.setattr(cal,'fetch_busy',forbidden)
    job=add(client,'我想取消預約')
    assert job['status']=='simulated' and job['intent']=='cancel'
    assert '未取消' in job['reply'] and job['calendar_result']['source']=='rules'


def test_time_only_answer_completes_previous_question():
    first=understand('我11月5日攞鎖匙，想同日驗樓')
    second=understand('上午11點',first)
    assert second['schedule']['pickup_at']=='2026-11-05T11:00:00+08:00'


def test_date_weekday_descriptor_does_not_replace_explicit_date():
    data=understand('我11月19日星期四上午11點領匙，想同日驗樓')
    assert data['schedule']['start_date']=='2026-11-19'


def test_completion_form_is_prefilled_from_business_window():
    data=understand('我11月5日成交，應該幾時攞鎖匙同驗樓？')
    assert data['schedule']['start_date']=='2026-11-10'
    assert data['schedule']['plan_from_completion']


def test_model_can_classify_unusual_wording_using_verified_spans(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER','deepseek');monkeypatch.setenv('DEEPSEEK_API_KEY','fake-only')
    class Model:
        def __init__(self,**kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':'{"intent":"availability","inspection_date":"11月5日"}'}}]})
    monkeypatch.setattr(intent.httpx,'AsyncClient',Model)
    data=understand('想11月5日搵你哋夾個時間去睇單位')
    assert data['provider']=='deepseek' and data['schedule']['start_date']=='2026-11-05'


def test_worker_no_slots_returns_no_invented_recommendation(client,busy):
    busy.append((datetime(2026,11,5,0,tzinfo=cal.HK),datetime(2026,11,6,0,tzinfo=cal.HK)))
    job=add(client,'我11月5日上午11點領匙，想同日驗樓')
    assert job['calendar_result']['status']=='no_slots'
    assert '未搵到合適' in job['reply']


def test_automatic_sunday_pickup_gets_specific_clarification(client,busy):
    job=add(client,'我11月8日上午11點領匙，想同日驗樓')
    assert '星期日唔開門' in job['reply']
    assert job['calendar_result']['source']=='rules'


def test_worker_business_planning_uses_google_results(client,busy):
    job=add(client,'我11月5日成交，應該幾時攞鎖匙同驗樓？')
    assert job['calendar_result']['source']=='google'
    assert '向管理處查詢11月10日領匙' in job['reply']
    assert job['calendar_result']['planning']['earliest_pickup']=='2026-11-10'


def test_automatic_path_stays_simulated_when_live_flag_set(client,busy,monkeypatch):
    monkeypatch.setenv('LIVE_SEND_ENABLED','true')
    job=add(client,'想約11月5日下午驗樓')
    assert job['status']=='simulated'


def test_reset_context_is_part_of_dedup_payload(client,busy):
    job=add(client,'想約11月5日驗樓',message_id='dedup-reset')
    response=client.post('/api/test/messages',json={'message':job['message'],'message_id':'dedup-reset','reset_context':True})
    assert response.status_code==409
