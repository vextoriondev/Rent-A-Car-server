"""Notification delivery (M7): turns an outbox row into one delivery per channel and sends them through adapters.

An outbox row is created by `helpers.notice` inside the business transaction. The deliveries are planned in the same
transaction and sent only after it commits (`dispatch`), so a rolled-back payment sends nothing. Per trigger there are
two audiences: the affected party (by consent and contact details) and the admin (always, from `OrgSettings`), each
on SMS and email. A party with no usable contact gets a visible `skipped_no_contact` row, never a silent drop.

Adapters never raise and never claim more than they know: `sent` means the provider accepted the message, the console
adapters (no provider connected) always report a failure that says so. Content comes only from the message catalog and
the party's own name, so a notice can never carry another party's figures or commercial terms."""
import logging
import threading
from datetime import timedelta
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone
from ..i18n import has_code, translate
from ..models import NotificationDelivery, NotificationOutbox, OrgSettings
from .email_resend import send_email

log = logging.getLogger(__name__)
CHANNELS=('sms','email')
D=NotificationDelivery.State

class SendResult:
    def __init__(self,sent,provider,message_id='',error=''):self.sent,self.provider,self.message_id,self.error=sent,provider,message_id,error

def send_sms(to,text):
    """No Bangladesh SMS provider is connected yet (the user will name one); this reports exactly that. Replace the body, keep the contract."""
    log.info('SMS not sent (no provider connected): to=%s',to)
    return SendResult(False,'console',error='No SMS provider is connected. No SMS was sent.')

def render(outbox,delivery):
    """(subject, text) in the language of the person who reads it: the party's own for their copy, English for the admin's."""
    lang='en' if delivery.is_admin_copy else outbox.recipient.language
    code=outbox.event_type
    title=translate('notice_title_'+code,None,lang) if has_code('notice_title_'+code) else code.replace('_',' ').capitalize()
    text=translate('notice_detail_'+code,{},lang) if has_code('notice_detail_'+code) else title
    if delivery.is_admin_copy:text=translate('notice_admin_for',{'party':outbox.recipient.name,'text':text},lang)
    return title,text

def _address(party,channel):
    if channel=='email':return party.email if party.consent_email else ''
    return party.phone if party.consent_sms else ''

def plan(outbox):
    """Create the deliveries for a new outbox row (idempotent) and return the ids to send."""
    party=outbox.recipient
    cfg=OrgSettings.objects.get_or_create(organization=outbox.organization)[0]
    usable=[(c,_address(party,c)) for c in CHANNELS if _address(party,c)]
    rows=[(c,False,a) for c,a in usable] or [('none',False,'')]
    rows+=[('sms',True,cfg.admin_copy_phone),('email',True,cfg.admin_copy_email)]
    ids=[]
    for channel,admin,addr in rows:
        state=D.QUEUED if addr else D.SKIPPED
        d,created=NotificationDelivery.objects.get_or_create(outbox=outbox,channel=channel,is_admin_copy=admin,defaults={'to_address':addr,'state':state})
        if created and state==D.QUEUED:ids.append(d.pk)
    summarize(outbox)
    return ids

def summarize(outbox):
    """The outbox row's state as one word for the list: queued while anything is waiting, failed if any send failed, else sent, else skipped."""
    ds=list(outbox.deliveries.all())
    states={d.state for d in ds}
    state='queued' if states&{D.QUEUED,D.SENDING} else 'failed' if D.FAILED in states else 'sent' if D.SENT in states else 'skipped_no_contact' if ds else outbox.state
    err=next((d.last_error for d in ds if d.state==D.FAILED),'')
    NotificationOutbox.objects.filter(pk=outbox.pk).update(state=state,attempts=max((d.attempts for d in ds),default=outbox.attempts),last_error=err)

def deliver(delivery_id):
    """Send one delivery. Safe to call twice: only a queued (or stuck sending) row is claimed, under a row lock."""
    with transaction.atomic():
        d=NotificationDelivery.objects.select_for_update(of=('self',)).select_related('outbox__recipient','outbox__organization').filter(pk=delivery_id).first()
        if not d or d.state not in (D.QUEUED,D.SENDING):return None
        d.state=D.SENDING;d.attempts+=1;d.save(update_fields=['state','attempts','updated_at'])
    title,text=render(d.outbox,d)
    result=send_email(d.to_address,title,text) if d.channel=='email' else send_sms(d.to_address,text)
    d.state=D.SENT if result.sent else D.FAILED
    d.provider,d.provider_message_id,d.last_error=result.provider,result.message_id,'' if result.sent else result.error
    d.sent_at=timezone.now() if result.sent else None
    d.save(update_fields=['state','provider','provider_message_id','last_error','sent_at','updated_at'])
    summarize(d.outbox)
    return d.state

def deliver_many(ids):
    for i in ids:
        try:deliver(i)
        except Exception:log.exception('Notice delivery %s crashed',i)   # one bad message must not stop the others; the row stays visible and retryable

def _run_in_thread(ids):
    def work():
        try:deliver_many(ids)
        finally:close_old_connections()
    threading.Thread(target=work,daemon=True,name='notice-delivery').start()

def dispatch(ids):
    """Send after the surrounding transaction commits. NOTICE_DELIVERY: `thread` (default, no broker needed), `celery`, `inline` (tests) or `off` (the sweeper sends)."""
    if not ids:return
    mode=settings.NOTICE_DELIVERY
    def go():
        if mode=='off':return
        if mode=='inline':return deliver_many(ids)
        if mode=='celery':
            from ..tasks import deliver_notice
            for i in ids:deliver_notice.delay(i)
            return
        _run_in_thread(ids)
    transaction.on_commit(go)

def requeue(outbox):
    """Retry button: failed deliveries (and stuck ones) go back to queued. Sent ones are never sent twice."""
    ids=list(outbox.deliveries.filter(state__in=[D.FAILED,D.SENDING]).values_list('pk',flat=True))
    outbox.deliveries.filter(pk__in=ids).update(state=D.QUEUED,last_error='')
    summarize(outbox);dispatch(ids)
    return ids

def sweep(older_than_seconds=300):
    """Send what is still queued (a crash between commit and send, or `off` mode) and unstick `sending` rows. Run it from cron or a Celery beat."""
    cutoff=timezone.now()-timedelta(seconds=older_than_seconds)
    ids=list(NotificationDelivery.objects.filter(state__in=[D.QUEUED,D.SENDING],updated_at__lt=cutoff).values_list('pk',flat=True))
    deliver_many(ids);return len(ids)

def channels():
    """Which channels really reach people right now. SMS stays off until a provider is wired into `send_sms`."""
    from .email_resend import resend_configured
    return {'email':'connected' if resend_configured() else 'not_connected','sms':'not_connected'}

def delivery_status():
    c=channels()
    return 'connected' if all(v=='connected' for v in c.values()) else 'partly_connected' if 'connected' in c.values() else 'not_connected'
