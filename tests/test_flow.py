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
    monkeypatch.setenv('LIVE_SEND_ENABLED','false')
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


def test_webhook_rejects_future_and_milliseconds(client):
    h={'X-Webhook-Secret':'test-webhook'}
    assert client.post('/webhooks/sleekflow',headers=h,json=normalized(timestamp=time.time()*1000)).status_code==422


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
