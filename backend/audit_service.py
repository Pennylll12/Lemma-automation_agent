"""Observable execution facts, readable reports and durable SMTP notifications.

Records application inputs/results and rule decisions, not model private reasoning.
No tokens, credential files, transport headers or raw provider errors are stored.
"""
import asyncio
from contextvars import ContextVar
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
import ssl
import time

from zoneinfo import ZoneInfo

HK=ZoneInfo('Asia/Hong_Kong')

ACTIVE = ContextVar('audit_run', default=None)
WRITERS = {}
REASONS = {
    'available':'候選通過', 'weekend_only':'只查週末', 'weekday_evening':'平日晚場未啟用',
    'preference':'不符合客人時段偏好', 'past':'時段已過', 'pickup_buffer':'不足領匙後一小時',
    'busy':'與Google忙碌／交通緩衝重疊', 'meal':'與用餐重疊', 'pickup_window':'無法配合估算領匙窗口',
}
STAGES = {'input':'收到訊息','intent':'意圖及資料識別','query':'實際查詢條件',
          'planning':'成交日領匙估算','calendar_request':'Google查詢範圍',
          'calendar_http':'Google HTTP狀態','busy':'Google返回忙碌區間',
          'policy':'本次套用規則','candidates':'場次篩選結果','selection':'推薦方案及回覆',
          'result':'任務結果','model_request':'傳給DeepSeek的內容','model_response':'DeepSeek核對後回傳',
          'model_failure':'模型備援處理','context':'日期基準與同客人上下文',
          'delivery':'回覆提交與防重複發送'}
FIELDS={'start_date':'查詢起日','days':'查詢天數','duration_minutes':'預留分鐘',
        'pickup_at':'已確認領匙時間','completion_date':'成交日','preference':'時段偏好',
        'include_weekday_evening':'包含平日晚場','plan_from_completion':'由成交日規劃',
        'weekend_only':'只查週末','pickup_date':'領匙日','pickup_time':'領匙時間','inspection_date':'驗樓日'}
INTENTS={'availability':'驗樓空位','completion_advice':'成交日／領匙規劃','cancel':'取消，需人工',
         'reschedule':'改期，需人工','other':'其他問題'}


def safe(value):
    """Defense in depth for any future caller adding transport/config data."""
    if isinstance(value, dict):
        return {k:safe(v) for k,v in value.items() if not re.search(
            r'token|secret|password|authorization|private_key|credentials|cookie|api_key',k,re.I)}
    if isinstance(value,(list,tuple)): return [safe(v) for v in value]
    if isinstance(value,(str,int,float,bool)) or value is None: return value
    return value.isoformat() if hasattr(value,'isoformat') else str(value)


def begin(kind, job_id=None, attempt=1):
    run={'id':secrets.token_hex(12),'kind':kind,'job_id':job_id,'attempt':attempt,
         'created':time.time(),'status':'running','steps':[]}
    return run, ACTIVE.set(run)


def record(stage, data):
    run=ACTIVE.get()
    if run is not None:
        run['steps'].append({'at':datetime.now(HK).isoformat(),'stage':stage,'data':safe(data)})
        if run['id'] in WRITERS: WRITERS[run['id']]()


def watch(run, writer): WRITERS[run['id']]=writer


def close(run, context):
    WRITERS.pop(run['id'],None)
    ACTIVE.reset(context)


def summary(run):
    status=run.get('status','running')
    titles={'simulated':'測試完成，未發WhatsApp','accepted':'WhatsApp API接受，未確認送達',
            'callback_accepted':'SleekFlow回覆Webhook接受，未確認WhatsApp送達',
            'failed':'處理失敗','handoff':'人工接管','needs_review':'需要人工核對',
            'window_closed':'發送窗口已關閉','running':'尚未完成','ok':'預覽完成'}
    lines=['叻媽自動化執行報告',f"任務：{run.get('job_id') or '獨立預覽'}",
           f"執行編號：{run['id']}；第{run['attempt']}次嘗試",
           f"時間：{datetime.fromtimestamp(run['created'],HK):%Y-%m-%d %H:%M:%S} 香港時間",
           '結果：'+titles.get(status,status),'',
           '本報告記錄可核對的輸入、回傳及規則判斷；沒有模型隱藏推理。']
    if run.get('sample_note'): lines.insert(0,run['sample_note'])
    if run.get('finished'):
        lines.insert(5,f"耗時：{max(0,run['finished']-run['created']):.1f}秒")
    blocks=[x for s in run['steps'] if s['stage']=='busy' for x in s['data']['intervals']
            if (datetime.fromisoformat(x['end'])-datetime.fromisoformat(x['start'])).total_seconds()>=86400]
    lines += ['', '優先核對']
    lines += [f"- 發現連續一天或以上的忙碌：{x['start']} → {x['end']}；請檢查是否只是無關驗樓的全天／跨日備註。" for x in blocks]
    decisions=[d for s in run['steps'] if s['stage']=='candidates' for d in s['data']]
    if decisions:
        lines.append('- 各日通過／排除數：')
        for day in dict.fromkeys(x['start'][:10] for x in decisions):
            counts={}
            for d in decisions:
                if d['start'].startswith(day): counts[d['reason']]=counts.get(d['reason'],0)+1
            lines.append('  '+day+'：'+'；'.join(REASONS.get(k,k)+str(v)+'場' for k,v in counts.items()))
    lines.append('- 飯點、交通及容量仍需核實；「查無衝突」不代表已保留預約。')
    for step in run['steps']:
        stage,data=step['stage'],step['data']
        lines += ['',STAGES.get(stage,stage)]
        if stage=='intent':
            lines.append('識別：'+INTENTS.get(data['intent'],data['intent'])+'；來源：'+data['provider'])
            lines += [f"- {FIELDS.get(k,k)}：{v}" for k,v in data.get('fields',{}).items()]
            lines += ['- 待追問：'+q for q in data.get('questions',[])]
            lines += ['- 提醒：'+n for n in data.get('notes',[])]
        elif stage=='query':
            for k,v in data.items():
                value='未提供' if v is None else '是' if v is True else '否' if v is False else v
                if k=='preference': value={'any':'不限','morning':'上午','afternoon':'下午','evening':'只查晚場'}.get(v,v)
                lines.append(f'- {FIELDS.get(k,k)}：{value}')
        elif stage=='context':
            lines += ['日期基準：'+data['anchor_date'],'可用的上一筆任務：'+(data['available_previous_job'] or '無'),data['note']]
        elif stage=='planning':
            lines += ['最快領信估算：'+data['letter_date'],'最早領匙估算：'+data['earliest_pickup'],
                      '領匙窗口邊界估算：'+data['pickup_deadline'],data['assumption'],'實際領信及管理處安排尚未確認。']
        elif stage=='calendar_request':
            lines += ['日曆：'+data['calendar_id'],'查詢範圍：'+data['timeMin']+' → '+data['timeMax'],
                      '時區：'+data['timezone'],'實際查詢條件：'+json.dumps(data['effective_query'],ensure_ascii=False)]
        elif stage=='calendar_http': lines.append('Google HTTP狀態：'+str(data['status']))
        elif stage=='policy':
            lines += ['候選開始時間：'+'／'.join(data['starts']),f"每場預留{data['duration_minutes']}分鐘；領匙緩衝{data['pickup_buffer_minutes']}分鐘",
                      '交通緩衝：'+('未配置' if data['travel_minutes'] is None else str(data['travel_minutes'])+'分鐘'),
                      '用餐安排：'+(data['meal_breaks'] or '未配置')]
        elif stage=='input':
            lines.append(data['masked_message'])
            lines.append('來源：'+data['mode']+'；'+('人工覆寫表單' if data.get('manual_schedule') else '自動識別'))
            if data.get('received_at'):
                lines.append('日期基準：'+data['received_at']+'；時間來源：'+data.get('time_source','legacy'))
                if data.get('time_source')=='server_received':
                    lines.append('採後端收到Webhook的時間；未驗證客人原始WhatsApp發訊時間。')
        elif stage=='model_request':
            lines.append('用途：'+data['purpose']+'；模型：'+data['model'])
            lines.append('已遮蓋訊息：'+data['masked_message'])
            lines.append('完整提示詞與結構化資料見JSON附件；沒有傳送日曆事件詳情。')
        elif stage=='busy':
            lines.append(f"日曆：{data['calendar_id']}；共{len(data['intervals'])}段忙碌")
            lines += [f"- {x['start']} → {x['end']}" for x in data['intervals']]
        elif stage=='candidates':
            lines += [f"- {x['start']} → {x['end']}：{REASONS.get(x['reason'],x['reason'])}"
                      + (f"；衝突：{json.dumps(x['conflicts'],ensure_ascii=False)}" if x.get('conflicts') else '') for x in data]
        elif stage=='selection':
            lines.append(data['reply'])
        elif stage=='result' and 'status' in data:
            lines.append('任務結果：'+titles.get(data['status'],data['status']))
            if data.get('error'): lines.append('錯誤：'+data['error'])
            if data.get('reply') and not any(s['stage']=='selection' for s in run['steps']): lines.append(data['reply'])
        else:
            lines.append(json.dumps(data,ensure_ascii=False,indent=2))
    lines += ['', '注意：日曆候選不等於已保留預約；登記、管理處安排及驗樓師容量需另核對。']
    return '\n'.join(lines)+'\n'


def message(run, sender=None, recipient=None):
    sender=sender or os.getenv('SMTP_FROM','')
    recipient=recipient or os.getenv('ADMIN_EMAIL_TO','')
    for address in (sender,recipient):
        if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',address):
            raise ValueError('Invalid mail address')
    msg=EmailMessage()
    msg['From']=sender;msg['To']=recipient
    msg['Subject']=f"[叻媽流程][{run['status']}] 第{run['attempt']}次執行 {run['id'][:8]}"
    msg['Date']=format_datetime(datetime.fromtimestamp(run['created'],HK))
    msg['Message-ID']=f"<supermama-{run['id']}@{sender.split('@')[1]}>"
    msg.set_content(summary(run))
    msg.add_attachment(json.dumps(run,ensure_ascii=False,indent=2).encode(),
                       maintype='application',subtype='json',filename=f"audit-{run['id']}.json")
    return msg


def save(db, run, db_path, status=None):
    if status is not None:
        run['status']=status;run['finished']=time.time()
    text=json.dumps(safe(run),ensure_ascii=False)
    enabled=os.getenv('AUDIT_EMAIL_ENABLED','false').lower()=='true'
    state='pending' if status is not None and run['kind']=='job' and enabled else 'disabled'
    with db() as c:
        c.execute('''INSERT INTO audit_runs(id,job_id,attempt,kind,created,finished,status,data,mail_state)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET finished=excluded.finished,
            status=excluded.status,data=excluded.data,
            mail_state=CASE WHEN audit_runs.status='running' THEN excluded.mail_state ELSE audit_runs.mail_state END''',
            (run['id'],run['job_id'],run['attempt'],run['kind'],run['created'],run.get('finished'),run['status'],text,state))
    if status is not None:
        # SQLite is authoritative; files can always be downloaded/regenerated.
        folder=Path(os.getenv('AUDIT_LOG_DIR') or Path(db_path).parent/'audit')
        try:
            folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            exports=[('json',json.dumps(safe(run),ensure_ascii=False,indent=2)),('txt',summary(run))]
            if os.getenv('SMTP_FROM') and os.getenv('ADMIN_EMAIL_TO'):
                exports.append(('eml',message(run).as_string()))
            for suffix,body in exports:
                target=folder/(run['id']+'.'+suffix)
                # Exclusive temp files avoid shared-directory symlinks or partial reports.
                temp=folder/(run['id']+'.'+secrets.token_hex(6)+'.tmp')
                with temp.open('x',encoding='utf-8') as f:
                    os.chmod(temp,0o600);f.write(body)
                temp.replace(target)
        except (OSError,ValueError):
            with db() as c: c.execute("UPDATE audit_runs SET file_error='匯出檔案未完成；請從工作台下載資料庫內日誌' WHERE id=?",(run['id'],))


def smtp_config():
    host=os.getenv('SMTP_HOST','smtp.gmail.com');port=int(os.getenv('SMTP_PORT','587'))
    user=os.getenv('SMTP_USER','');password=os.getenv('SMTP_PASSWORD','')
    mode=os.getenv('SMTP_SECURITY','starttls').lower()
    if not host or not user or not password or not 1<=port<=65535 or mode not in ('ssl','starttls'):
        raise ValueError('SMTP尚未完整配置，請填寄件帳戶及應用程式密碼')
    return host,port,user,password,mode


class MailNotSubmitted(Exception):
    """Transport/authentication failed before DATA; safe to retry explicitly."""


def smtp_send(msg, config):
    host,port,user,password,mode=config
    context=ssl.create_default_context()
    client=None
    try:
        try:
            client=smtplib.SMTP_SSL(host,port,timeout=15,context=context) if mode=='ssl' else smtplib.SMTP(host,port,timeout=15)
            if mode=='starttls': client.ehlo();client.starttls(context=context);client.ehlo()
            client.login(user,password)
        except Exception: raise MailNotSubmitted() from None
        rejected=client.send_message(msg)
        if rejected: raise RuntimeError('Recipient refused')
    finally:
        # Successful DATA response remains accepted even if QUIT disconnects.
        if client is not None:
            try: client.quit()
            except Exception:
                try: client.close()
                except Exception: pass


async def send_one(db):
    if os.getenv('AUDIT_EMAIL_ENABLED','false').lower()!='true': return False
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT * FROM audit_runs WHERE mail_state='pending' ORDER BY created LIMIT 1").fetchone()
        if not row: return False
        c.execute("UPDATE audit_runs SET mail_state='sending' WHERE id=?",(row['id'],))
    try:
        config=smtp_config();msg=message(json.loads(row['data']))
    except (ValueError,TypeError):
        state,error='config_error','SMTP或寄收件地址未配置正確；修正後可安全重試通知'
    else:
        try:
            await asyncio.to_thread(smtp_send,msg,config)
            state,error='accepted',None
        except MailNotSubmitted:
            state,error='config_error','SMTP連線或認證未完成，郵件未提交；檢查SMTP設定及應用程式密碼後可重試通知'
        except Exception:
            state,error='unknown','郵件發送結果未確認；先查收件匣，禁止自動重寄'
    with db() as c: c.execute('UPDATE audit_runs SET mail_state=?,mail_error=? WHERE id=?',(state,error,row['id']))
    return True
