import asyncio
import time
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from backend import app as m


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'DB',str(tmp_path/'test.db'))
    monkeypatch.setattr(m,'ADMIN_TOKEN','test-admin')
    monkeypatch.setattr(m,'WEBHOOK_SECRET','test-webhook')
    monkeypatch.setattr(m,'ALLOWLIST',{'85290000001'})
    monkeypatch.setenv('AI_PROVIDER','mock')
    monkeypatch.setenv('CALENDAR_MODE','mock')
    monkeypatch.setenv('LIVE_SEND_ENABLED','false')
    monkeypatch.setenv('SLEEKFLOW_REPLY_WEBHOOK_ENABLED','false')
    monkeypatch.setenv('EMBEDDED_WORKER','false')
    with TestClient(m.app) as c:
        c.headers['Authorization']='Bearer test-admin'
        yield c


def add(c,**kwargs):
    payload={'message':'星期六有冇驗樓位？','message_id':'msg-1',**kwargs}
    res=c.post('/api/test/messages',json=payload)
    assert res.status_code==200,res.text
    return res.json()


def run():
    return asyncio.run(m.process_one())


def test_admin_required(client):
    assert client.get('/api/jobs',headers={'Authorization':''}).status_code==401
    assert client.get('/health').json()['ok']


def test_durable_enqueue_and_duplicate(client):
    assert not add(client)['duplicate']
    assert add(client)['duplicate']
    assert len(client.get('/api/jobs').json()['jobs'])==1
    assert client.post('/api/test/messages',json={'message':'different','message_id':'msg-1'}).status_code==409


def test_worker_mock_completion(client):
    add(client)
    assert run()
    j=client.get('/api/jobs').json()['jobs'][0]
    assert j['status']=='simulated' and j['stage']==4
    assert '未建立預約' in j['reply']
    assert not run()


def test_fail_retry_once_and_no_retry_sent(client):
    add(client,fail_once=True)
    run()
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='failed'
    assert client.post('/api/jobs/msg-1/retry',json={}).status_code==200
    run()
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='simulated'
    assert client.post('/api/jobs/msg-1/retry',json={}).status_code==409


def test_per_contact_serial_claim(client):
    add(client)
    add(client,message_id='msg-2')
    add(client,message_id='msg-3',contact_id='test-002')
    first=m.claim_job()
    second=m.claim_job()
    assert first['id']=='msg-1' and second['id']=='msg-3'
    assert m.claim_job() is None


def test_human_takeover_interrupts_queued_and_pre_send(client):
    add(client)
    j=m.claim_job()
    client.post('/api/contacts/test-001/takeover',json={'human':True})
    asyncio.run(m.deliver(j,'test reply'))
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='handoff'
    with m.db() as c: assert c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0
    add(client,message_id='msg-2')
    assert m.claim_job() is None


def test_closed_window_blocks_free_text(client):
    m.enqueue('old','test-001','驗樓','channel',time.time()-90000,'test')
    run()
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='window_closed'


def test_duplicate_does_not_reopen_window(client):
    old=time.time()-90000
    m.enqueue('old','test-001','驗樓','channel',old,'test')
    m.enqueue('old','test-001','驗樓','channel',time.time(),'test')
    with m.db() as c: assert c.execute('SELECT last_inbound FROM contacts').fetchone()[0]==old


def test_recover_crashed_worker_before_send(client):
    add(client)
    first=m.claim_job()
    with m.db() as c: c.execute('UPDATE jobs SET lease_until=?',(time.time()-1,))
    second=m.claim_job()
    assert second['id']==first['id'] and second['claim']!=first['claim']
    m.update_job(first,status='failed')
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='processing'


def test_recover_uncertain_delivery_never_resends(client):
    add(client)
    m.claim_job()
    with m.db() as c:
        c.execute('UPDATE jobs SET lease_until=?',(time.time()-1,))
        c.execute("INSERT INTO outbox VALUES('msg-1','sending',NULL,?)",(time.time(),))
    assert m.claim_job() is None
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='needs_review'
    assert client.post('/api/jobs/msg-1/retry',json={}).status_code==409


def normalized(**kw):
    return {'message_id':'incoming-1','contact_id':'85290000001','message':'驗樓','channel':'test-channel','timestamp':time.time(),'direction':'inbound',**kw}


def test_webhook_contract_auth_and_allowlist(client):
    assert client.post('/webhooks/sleekflow',json=normalized()).status_code==401
    h={'X-Webhook-Secret':'test-webhook'}
    assert client.post('/webhooks/sleekflow',headers=h,json={}).status_code==422
    assert client.post('/webhooks/sleekflow',headers=h,json=normalized(direction='outbound')).json()['ignored']
    assert client.post('/webhooks/sleekflow',headers=h,json=normalized(contact_id='85291111111')).json()['ignored']
    assert client.post('/webhooks/sleekflow',headers=h,json=normalized()).json()['accepted']


def test_webhook_uses_receipt_time_without_upstream_timestamp(client):
    h={'X-Webhook-Secret':'test-webhook'}
    body=normalized();body.pop('timestamp')
    before=time.time()
    assert client.post('/webhooks/sleekflow',headers=h,json=body).json()['accepted']
    with m.db() as db:
        j=dict(db.execute('SELECT * FROM jobs').fetchone())
        inbound=db.execute('SELECT last_inbound FROM contacts').fetchone()[0]
    assert before<=j['received_at']<=time.time()
    assert inbound==j['received_at'] and j['time_source']=='server_received'
    assert client.post('/webhooks/sleekflow',headers=h,json=body).json()['duplicate']
    with m.db() as db:
        assert db.execute('SELECT last_inbound FROM contacts').fetchone()[0]==inbound


@pytest.mark.parametrize('body,expected', [
    ({}, 'Missing required fields:'),
    (normalized(message_id='', message='private-customer-text'), 'Fields must be non-empty strings: message_id'),
    (normalized(channel=None), 'Fields must be non-empty strings: channel'),
])
def test_webhook_rejection_logs_reason_without_payload(client, caplog, body, expected):
    with caplog.at_level('WARNING', logger='uvicorn.error'):
        response=client.post('/webhooks/sleekflow',headers={'X-Webhook-Secret':'test-webhook'},json=body)
    assert response.status_code==422
    assert expected in response.json()['detail']
    assert expected in caplog.text
    assert 'private-customer-text' not in caplog.text
    assert 'private-invalid-time' not in caplog.text
    assert 'test-webhook' not in caplog.text


def test_webhook_invalid_json_logs_position_without_body(client, caplog):
    with caplog.at_level('WARNING', logger='uvicorn.error'):
        response=client.post('/webhooks/sleekflow',headers={'X-Webhook-Secret':'test-webhook'},content='{"message":"private-customer-text"oops}')
    assert response.status_code==422
    assert 'Invalid JSON at line 1, column' in caplog.text
    assert 'private-customer-text' not in caplog.text


def test_masks_common_pii_before_storage(client):
    add(client,message='我叫陳小姐，電話91234567，A座1201室，a@example.com')
    j=client.get('/api/jobs').json()['jobs'][0]
    assert all(s not in j['message'] for s in ['陳小姐','91234567','1201','a@example.com'])


def test_test_entry_never_sends_even_with_live_flag(client,monkeypatch):
    monkeypatch.setenv('LIVE_SEND_ENABLED','true')
    add(client)
    run()
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='simulated'


def test_template_substitution_preserves_json_types(client):
    reply='換行\n以及"引號"'
    assert m.render_template({'content':'{{reply}}','to':'{{recipient}}','n':1},{'reply':reply,'recipient':'85290000001'})=={'content':reply,'to':'85290000001','n':1}


def test_provider_failure_does_not_expose_secrets(client,monkeypatch):
    async def broken(j): raise RuntimeError('SECRET-MUST-NOT-LOG')
    monkeypatch.setattr(m,'answer',broken)
    add(client)
    run()
    j=client.get('/api/jobs').json()['jobs'][0]
    assert j['status']=='failed'
    assert 'SECRET-MUST-NOT-LOG' not in str(j)


def test_separate_worker_process_and_persistence(client):
    add(client)
    env={**os.environ,'DATABASE_PATH':m.DB,'AI_PROVIDER':'mock','LIVE_SEND_ENABLED':'false'}
    worker=subprocess.Popen([sys.executable,'-m','backend.app','worker'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if client.get('/api/jobs').json()['jobs'][0]['status']=='simulated': break
            if worker.poll() is not None: pytest.fail(worker.stderr.read().decode())
            time.sleep(.1)
        else: pytest.fail('Independent worker did not finish task')
    finally:
        worker.terminate()
        worker.wait(timeout=5)
        worker.stderr.close()
    # A fresh connection after process exit reads the same persisted result.
    with m.db() as c:
        assert c.execute('SELECT status FROM jobs').fetchone()[0]=='simulated'


@pytest.mark.parametrize('value',['04/11/2026 12:30','2026-11-04T12:30:00',True,None,{},'NaN','inf',0,9999999999999])
def test_upstream_timestamp_is_ignored_and_cannot_change_receipt_time(client,value):
    before=time.time()
    r=client.post('/webhooks/sleekflow',headers={'X-Webhook-Secret':'test-webhook'},json=normalized(timestamp=value))
    assert r.status_code==200 and r.json()['accepted']
    with m.db() as db:
        assert before<=db.execute('SELECT received_at FROM jobs').fetchone()[0]<=time.time()


def test_webhook_form_preserves_newlines_quotes_and_plus(client):
    h={'X-Webhook-Secret':'test-webhook'}
    message='想約時間驗樓：成交日期有啦\n4/11成交，想問"下午" + 週末 & 時間？'
    body=normalized(message=message);body.pop('timestamp')
    r=client.post('/webhooks/sleekflow',headers=h,data=body)
    assert r.status_code==200 and r.json()['accepted']
    assert client.get('/api/jobs').json()['jobs'][0]['message']==message
    assert run()
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='simulated'


def test_webhook_form_does_not_bypass_auth_allowlist_or_validation(client):
    body=normalized();body.pop('timestamp')
    assert client.post('/webhooks/sleekflow',data=body).status_code==401
    h={'X-Webhook-Secret':'test-webhook'}
    assert client.post('/webhooks/sleekflow',headers=h,data={**body,'contact_id':'85291111111'}).json()['reason']=='not_allowlisted'
    assert client.post('/webhooks/sleekflow',headers=h,data={**body,'message':''}).status_code==422
    assert client.post('/webhooks/sleekflow',headers={**h,'Content-Type':'application/x-www-form-urlencoded'},content='message=a&message=b').status_code==422
    assert client.post('/webhooks/sleekflow',headers={**h,'Content-Type':'text/plain'},content='hello').status_code==415
    assert not client.get('/api/jobs').json()['jobs']


def callback_settings(monkeypatch):
    monkeypatch.setenv('SLEEKFLOW_REPLY_WEBHOOK_ENABLED','true')
    monkeypatch.setenv('SLEEKFLOW_REPLY_WEBHOOK_URL','https://callback.example.test/flow')
    monkeypatch.setenv('SLEEKFLOW_REPLY_WEBHOOK_HEADERS','{"X-Test-Trigger":"test-trigger", "X-Test-Token":"callback-secret"}')


def test_callback_posts_once_with_safe_json_and_reports_acceptance(client,monkeypatch):
    import json
    import httpx
    callback_settings(monkeypatch)
    real_client=httpx.AsyncClient
    calls=[]
    def receiver(request):
        assert str(request.url)=='https://callback.example.test/flow'
        assert request.headers['X-Test-Token']=='callback-secret'
        body=json.loads(request.content);calls.append(body)
        assert request.headers['Idempotency-Key']==body['event_id']
        return httpx.Response(200,json={'ok':True})
    monkeypatch.setattr(m.httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(receiver),**kw))
    h={'X-Webhook-Secret':'test-webhook'}
    body=normalized();body.pop('timestamp')
    assert client.post('/webhooks/sleekflow',headers=h,json=body).json()['accepted']
    reply='你好呀！\n可揀"下午"，確認後再落實。'
    async def answer(job):
        m.update_job(job,reply=reply,stage=3)
        return reply
    monkeypatch.setattr(m,'answer',answer)
    assert run()
    assert calls[0]['reply']==reply and calls[0]['phone']==body['contact_id']
    assert calls[0]['time_source']=='server_received'
    assert calls[0]['received_at'].endswith('+08:00')
    assert set(calls[0])=={'event','event_id','message_id','contact_id','phone','channel','reply','received_at','processed_at','time_source'}
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='callback_accepted'
    assert client.post('/webhooks/sleekflow',headers=h,json=body).json()['duplicate']
    assert not run() and len(calls)==1
    assert client.post('/api/jobs/incoming-1/retry',json={}).status_code==409
    with m.db() as db:
        report=db.execute('SELECT data FROM audit_runs').fetchone()[0]
    assert 'server_received' in report and 'callback_accepted' in report
    assert 'callback-secret' not in report and 'callback.example.test' not in report


@pytest.mark.parametrize('failure',['timeout','http500','redirect'])
def test_callback_uncertain_or_failed_submission_never_automatically_resends(client,monkeypatch,failure):
    import httpx
    callback_settings(monkeypatch)
    real_client=httpx.AsyncClient
    calls=[]
    def receiver(request):
        calls.append(request)
        if failure=='timeout':raise httpx.ReadTimeout('private-token-and-url',request=request)
        return httpx.Response(302 if failure=='redirect' else 500,headers={'Location':'https://other.example.test'},text='private-token-and-url')
    monkeypatch.setattr(m.httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(receiver),**kw))
    client.post('/webhooks/sleekflow',headers={'X-Webhook-Secret':'test-webhook'},json=normalized())
    assert run()
    job=client.get('/api/jobs').json()['jobs'][0]
    assert job['status']=='needs_review' and 'private-token' not in str(job)
    assert client.post('/api/jobs/incoming-1/retry',json={}).status_code==409
    assert not run() and len(calls)==1
    with m.db() as db: assert db.execute('SELECT state FROM outbox').fetchone()[0]=='unknown'


@pytest.mark.parametrize('failure',['missing_url','bad_headers','both_enabled','worker_allowlist'])
def test_callback_config_errors_do_not_reserve_outbox_and_are_retryable(client,monkeypatch,failure):
    callback_settings(monkeypatch)
    client.post('/webhooks/sleekflow',headers={'X-Webhook-Secret':'test-webhook'},json=normalized())
    if failure=='missing_url':monkeypatch.delenv('SLEEKFLOW_REPLY_WEBHOOK_URL')
    elif failure=='bad_headers':monkeypatch.setenv('SLEEKFLOW_REPLY_WEBHOOK_HEADERS','{"X-Token":"secret\\nvalue"}')
    elif failure=='both_enabled':monkeypatch.setenv('LIVE_SEND_ENABLED','true')
    else:monkeypatch.setattr(m,'ALLOWLIST',{'85291111111'})
    assert run()
    job=client.get('/api/jobs').json()['jobs'][0]
    assert job['status']=='failed' and '未提交回覆' in job['error']
    with m.db() as db: assert db.execute('SELECT count(*) FROM outbox').fetchone()[0]==0
    assert client.post('/api/jobs/incoming-1/retry',json={}).status_code==200


def test_workbench_tests_never_callback_even_if_enabled(client,monkeypatch):
    callback_settings(monkeypatch)
    monkeypatch.setenv('LIVE_SEND_ENABLED','true')
    add(client)
    assert run()
    assert client.get('/api/jobs').json()['jobs'][0]['status']=='simulated'


@pytest.mark.parametrize('stop',['takeover','expired'])
def test_callback_respects_takeover_and_receipt_age(client,monkeypatch,stop):
    callback_settings(monkeypatch)
    m.enqueue('guard','85290000001','驗樓','test',time.time()-90000 if stop=='expired' else time.time(),'webhook')
    j=m.claim_job()
    if stop=='takeover':
        client.post('/api/contacts/85290000001/takeover',json={'human':True})
    asyncio.run(m.deliver(j,'reply'))
    job=client.get('/api/jobs').json()['jobs'][0]
    assert job['status']==('handoff' if stop=='takeover' else 'window_closed')
    with m.db() as db: assert db.execute('SELECT count(*) FROM outbox').fetchone()[0]==0


@pytest.mark.parametrize('url,headers',[
    ('http://callback.example.test','{"X-Token":"token"}'),
    ('https://user:password@callback.example.test','{"X-Token":"token"}'),
    ('https://callback.example.test','{}'),
    ('https://callback.example.test','{"Content-Length":"1"}'),
    ('https://callback.example.test','{"X-Token":null}'),
])
def test_callback_rejects_invalid_transport_configuration(monkeypatch,url,headers):
    monkeypatch.setenv('SLEEKFLOW_REPLY_WEBHOOK_URL',url)
    monkeypatch.setenv('SLEEKFLOW_REPLY_WEBHOOK_HEADERS',headers)
    with pytest.raises(m.reply_webhook.ReplyWebhookConfigError):m.reply_webhook.settings()
