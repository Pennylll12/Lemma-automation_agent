import asyncio
from datetime import datetime
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import smtplib

import pytest
from fastapi.testclient import TestClient
from backend import app as m, audit_service as audit, calendar_service as cal

NOW=datetime(2026,11,4,10,tzinfo=cal.HK)

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'DB',str(tmp_path/'flow.db'))
    monkeypatch.setattr(m,'ADMIN_TOKEN','test-admin')
    monkeypatch.setenv('AI_PROVIDER','mock')
    monkeypatch.setenv('CALENDAR_MODE','google')
    monkeypatch.setenv('CALENDAR_BUSINESS_WEEK','mon_sat')
    monkeypatch.setenv('LIVE_SEND_ENABLED','false')
    monkeypatch.setenv('EMBEDDED_WORKER','false')
    monkeypatch.setenv('AUDIT_EMAIL_ENABLED','false')
    monkeypatch.setenv('AUDIT_LOG_DIR',str(tmp_path/'reports'))
    monkeypatch.setenv('SMTP_FROM','sender@example.test')
    monkeypatch.setenv('ADMIN_EMAIL_TO','admin@example.test')
    monkeypatch.delenv('SMTP_USER',raising=False)
    monkeypatch.delenv('SMTP_PASSWORD',raising=False)
    monkeypatch.delenv('CALENDAR_TRAVEL_MINUTES',raising=False)
    monkeypatch.delenv('CALENDAR_MEAL_BREAKS',raising=False)
    monkeypatch.setattr(cal,'now_hk',lambda:NOW)
    monkeypatch.setattr(m.time,'time',lambda:NOW.timestamp())
    with TestClient(m.app) as c:
        c.headers['Authorization']='Bearer test-admin'
        yield c
    assert not audit.WRITERS

@pytest.fixture
def busy(monkeypatch):
    intervals=[(datetime(2026,11,9,tzinfo=cal.HK),datetime(2026,11,13,tzinfo=cal.HK))]
    async def fetch(start,end): return intervals
    monkeypatch.setattr(cal,'fetch_busy',fetch)
    return intervals


def run(client,**body):
    response=client.post('/api/test/messages',json={'message':'成交日期有啦，4/11成交',**body})
    assert response.status_code==200,response.text
    asyncio.run(m.process_one())
    return client.get('/api/jobs').json()['jobs'][0]


def fetch_report(client,run_id):
    return client.get('/api/audit/runs/'+run_id+'?format=json').json()


def test_full_data_flow_and_exact_busy_rejection_persisted(client,busy,tmp_path):
    job=run(client)
    assert job['status']=='simulated' and len(job['audit_runs'])==1
    rid=job['audit_runs'][0]['id'];report=fetch_report(client,rid)
    steps={s['stage']:s['data'] for s in report['steps']}
    assert steps['intent']['fields']['completion_date']=='2026-11-04'
    assert steps['calendar_request']['effective_query']['start_date']=='2026-11-09'
    assert steps['busy']['intervals'][0]['end']=='2026-11-13T00:00:00+08:00'
    decision=next(d for d in steps['candidates'] if d['start']=='2026-11-10T12:30:00+08:00')
    assert decision['reason']=='busy' and decision['conflicts'][0]['start'].startswith('2026-11-09')
    assert steps['selection']['reply']==job['reply']
    assert 'contact_id' not in steps['input']
    text=client.get('/api/audit/runs/'+rid).text
    assert '連續一天或以上的忙碌' in text and '2026-11-10' in text
    assert '與Google忙碌／交通緩衝重疊' in text
    for extension in ('json','txt','eml'):
        assert (tmp_path/'reports'/f'{rid}.{extension}').exists()
    with m.db() as c: assert c.execute('SELECT data FROM audit_runs WHERE id=?',(rid,)).fetchone()[0]


def test_reports_require_admin_and_do_not_expose_credentials(client,busy):
    job=run(client);rid=job['audit_runs'][0]['id']
    assert client.get('/api/audit/runs',headers={'Authorization':''}).status_code==401
    assert client.get('/api/audit/runs/'+rid,headers={'Authorization':''}).status_code==401
    assert client.get('/api/audit/runs/nonexistent').status_code==404
    assert client.get('/api/audit/runs').json()['runs'][0]['job_id']==job['id']
    assert client.get('/api/config').json()['audit_email']=={'enabled':False,'configured':False}
    assert audit.safe({'authorization':'SECRET','nested':{'api_key':'SECRET','ok':1},'SMTP_PASSWORD':'SECRET'})=={'nested':{'ok':1}}


def test_duplicate_does_not_create_another_report_or_email(client,busy,monkeypatch):
    monkeypatch.setenv('AUDIT_EMAIL_ENABLED','true')
    job=run(client,message_id='original')
    assert client.post('/api/test/messages',json={'message':job['message'],'message_id':'original'}).json()['duplicate']
    assert not asyncio.run(m.process_one())
    with m.db() as c:
        assert c.execute('SELECT COUNT(*) FROM audit_runs').fetchone()[0]==1
        assert c.execute('SELECT mail_state FROM audit_runs').fetchone()[0]=='pending'


def test_failure_and_retry_keep_separate_attempt_reports(client,busy):
    job=run(client,message_id='retry-me',fail_once=True)
    first=job['audit_runs'][0]['id']
    assert job['status']=='failed' and fetch_report(client,first)['status']=='failed'
    assert client.post('/api/jobs/retry-me/retry',json={}).status_code==200
    asyncio.run(m.process_one())
    job=client.get('/api/jobs').json()['jobs'][0]
    assert job['status']=='simulated' and [x['attempt'] for x in job['audit_runs']]==[2,1]
    assert fetch_report(client,first)['status']=='failed'


def test_google_failure_saves_query_without_raw_provider_error(client,monkeypatch):
    async def fail(*args): raise cal.CalendarError('Google日曆查詢失敗（HTTP 403）')
    monkeypatch.setattr(cal,'fetch_busy',fail)
    job=run(client)
    report=fetch_report(client,job['audit_runs'][0]['id'])
    assert report['status']=='failed'
    assert any(s['stage']=='calendar_request' for s in report['steps'])
    assert not any(s['stage']=='busy' for s in report['steps'])


def test_preview_is_logged_but_never_queued_for_mail(client,monkeypatch):
    monkeypatch.setenv('AUDIT_EMAIL_ENABLED','true')
    r=client.post('/api/intent/interpret',json={'message':'我11月5日領匙，想同日驗樓'})
    assert r.status_code==200 and r.json()['audit_run_id']
    rid=r.json()['audit_run_id'];report=fetch_report(client,rid)
    assert report['kind']=='intent_preview' and report['status']=='ok'
    assert client.get('/api/audit/runs').json()['runs'][0]['mail_state']=='disabled'
    assert not client.get('/api/jobs').json()['jobs']


def test_human_takeover_jobs_have_reports(client):
    client.post('/api/contacts/test-001/takeover',json={'human':True})
    r=client.post('/api/test/messages',json={'message':'想約驗樓','message_id':'human'})
    assert r.status_code==200
    j=client.get('/api/jobs').json()['jobs'][0]
    assert j['status']=='handoff' and j['audit_runs'][0]['attempt']==0
    assert fetch_report(client,j['audit_runs'][0]['id'])['status']=='handoff'


def test_queued_jobs_cancelled_by_takeover_are_logged(client):
    client.post('/api/test/messages',json={'message':'想約驗樓','message_id':'waiting'})
    client.post('/api/contacts/test-001/takeover',json={'human':True})
    j=client.get('/api/jobs').json()['jobs'][0]
    assert j['audit_runs'][0]['status']=='handoff'


def test_daily_rejection_reasons_include_preference_and_pickup(client,busy):
    busy.clear()
    job=run(client,message='我11月5日上午11點領匙，想同日下晝驗樓')
    report=fetch_report(client,job['audit_runs'][0]['id'])
    reasons=[d['reason'] for s in report['steps'] if s['stage']=='candidates' for d in s['data']]
    assert 'preference' in reasons and 'available' in reasons and 'weekday_evening' in reasons


def test_missing_mail_config_never_connects_and_retry_does_not_repeat_job(client,busy,monkeypatch):
    monkeypatch.setenv('AUDIT_EMAIL_ENABLED','true')
    job=run(client);rid=job['audit_runs'][0]['id']
    def forbidden(*args):raise AssertionError('No SMTP connection without configuration')
    monkeypatch.setattr(audit,'smtp_send',forbidden)
    assert asyncio.run(audit.send_one(m.db))
    assert client.get('/api/audit/runs').json()['runs'][0]['mail_state']=='config_error'
    assert client.post('/api/audit/runs/'+rid+'/retry-email',json={}).status_code==200
    assert client.get('/api/jobs').json()['jobs'][0]['attempts']==1
    assert not asyncio.run(m.process_one())


def configure(monkeypatch):
    monkeypatch.setenv('AUDIT_EMAIL_ENABLED','true')
    monkeypatch.setenv('SMTP_USER','sender@example.test')
    monkeypatch.setenv('SMTP_PASSWORD','test-application-password')
    monkeypatch.setenv('SMTP_HOST','smtp.example.test')
    monkeypatch.setenv('SMTP_PORT','587')
    monkeypatch.setenv('SMTP_SECURITY','starttls')


def test_smtp_tls_and_message_content_with_fake_transport(client,busy,monkeypatch):
    configure(monkeypatch);job=run(client);rid=job['audit_runs'][0]['id']
    calls=[]
    class FakeSMTP:
        def __init__(self,host,port,**kw):calls.append(('connect',host,port))
        def ehlo(self):calls.append(('ehlo',))
        def starttls(self,context):calls.append(('tls',context.check_hostname))
        def login(self,user,password):calls.append(('login',user))
        def send_message(self,msg):
            assert msg['From']=='sender@example.test' and msg['To']=='admin@example.test'
            body=msg.get_body(preferencelist=('plain',)).get_content()
            assert '連續一天或以上的忙碌' in body and '2026-11-10' in body
            attachment=list(msg.iter_attachments())[0]
            assert json.loads(attachment.get_content())['id']==rid
            assert 'test-application-password' not in msg.as_string()
            return {}
        def quit(self):pass
    monkeypatch.setattr(smtplib,'SMTP',FakeSMTP)
    asyncio.run(audit.send_one(m.db))
    assert ('tls',True) in calls
    assert client.get('/api/audit/runs').json()['runs'][0]['mail_state']=='accepted'
    assert not asyncio.run(audit.send_one(m.db))


def test_unknown_send_never_automatically_retries_or_resubmits_job(client,busy,monkeypatch):
    configure(monkeypatch);job=run(client);rid=job['audit_runs'][0]['id']
    calls=[]
    def timeout(*args):calls.append(1);raise TimeoutError('SECRET smtp password/provider body')
    monkeypatch.setattr(audit,'smtp_send',timeout)
    asyncio.run(audit.send_one(m.db))
    r=client.get('/api/audit/runs').json()['runs'][0]
    assert r['mail_state']=='unknown' and 'SECRET' not in r['mail_error']
    assert not asyncio.run(audit.send_one(m.db)) and len(calls)==1
    assert client.post('/api/audit/runs/'+rid+'/retry-email',json={}).status_code==409
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='simulated'


def test_two_workers_claim_only_one_notification(client,busy,monkeypatch):
    configure(monkeypatch);run(client);calls=[]
    monkeypatch.setattr(audit,'smtp_send',lambda *args:calls.append(1))
    async def send():return await asyncio.gather(audit.send_one(m.db),audit.send_one(m.db))
    assert sorted(asyncio.run(send()))==[False,True] and calls==[1]


def test_draft_email_is_downloadable_without_sending(client,busy):
    job=run(client);rid=job['audit_runs'][0]['id']
    r=client.get('/api/audit/runs/'+rid+'?format=eml')
    msg=BytesParser(policy=policy.default).parsebytes(r.content)
    assert msg['To']=='admin@example.test'
    assert client.get('/api/audit/runs').json()['runs'][0]['mail_state']=='disabled'


def test_test_email_is_explicit_and_does_not_enqueue_customer_task(client,monkeypatch):
    assert client.post('/api/audit/test-email',json={}).status_code==409
    monkeypatch.setenv('AUDIT_EMAIL_ENABLED','true')
    r=client.post('/api/audit/test-email',json={})
    assert r.status_code==200 and r.json()['mail_state']=='pending'
    assert not client.get('/api/jobs').json()['jobs']
    assert fetch_report(client,r.json()['audit_run_id'])['job_id']=='notification-check'


def test_checkpoint_exists_before_calendar_returns(client,monkeypatch):
    async def paused(*args):
        with m.db() as c:
            row=c.execute('SELECT data FROM audit_runs WHERE status=?',('running',)).fetchone()
        assert any(s['stage']=='calendar_request' for s in json.loads(row['data'])['steps'])
        return []
    monkeypatch.setattr(cal,'fetch_busy',paused)
    run(client)


def test_export_disk_failure_keeps_database_report(client,busy,tmp_path,monkeypatch):
    file=tmp_path/'not-a-folder';file.write_text('blocked')
    monkeypatch.setenv('AUDIT_LOG_DIR',str(file))
    job=run(client);r=job['audit_runs'][0]
    assert job['status']=='simulated' and r['file_error']
    assert fetch_report(client,r['id'])['status']=='simulated'


def test_auth_failure_is_known_unsent_and_can_retry(client,busy,monkeypatch):
    configure(monkeypatch);job=run(client);rid=job['audit_runs'][0]['id']
    class FailedSMTP:
        def __init__(self,*args,**kwargs):pass
        def ehlo(self):pass
        def starttls(self,**kwargs):pass
        def login(self,*args):raise smtplib.SMTPAuthenticationError(535,b'SECRET provider body')
        def quit(self):pass
    monkeypatch.setattr(smtplib,'SMTP',FailedSMTP)
    asyncio.run(audit.send_one(m.db))
    row=client.get('/api/audit/runs').json()['runs'][0]
    assert row['mail_state']=='config_error' and 'SECRET' not in row['mail_error']
    assert client.post('/api/audit/runs/'+rid+'/retry-email',json={}).status_code==200


def test_cancelled_worker_checkpoint_survives_and_recovery_queues_report(client,busy,monkeypatch):
    configure(monkeypatch)
    async def blocked(*args):
        asyncio.current_task().cancel()
        await asyncio.sleep(0)
    monkeypatch.setattr(cal,'fetch_busy',blocked)
    client.post('/api/test/messages',json={'message':'4/11成交','message_id':'interrupted'})
    with pytest.raises(asyncio.CancelledError):asyncio.run(m.process_one())
    rows=client.get('/api/audit/runs').json()['runs']
    assert rows[0]['status']=='interrupted'
    report=fetch_report(client,rows[0]['id'])
    assert any(s['stage']=='calendar_request' for s in report['steps'])
    assert not audit.WRITERS
