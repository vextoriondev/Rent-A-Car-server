"""M7: outbox rows become per-channel deliveries, sent only after commit, honestly recorded, consent-aware and retryable."""
from unittest import mock
from django.test import TestCase, override_settings
from django.db import transaction
from ..models import NotificationDelivery, NotificationOutbox, OrgSettings, Party
from .. import services
from ..services import notify
from ..services.email_resend import EmailResult
from .test_reports import ReportBase

OK=EmailResult(True,'resend',message_id='m-1')
BAD=EmailResult(False,'resend',error='Resend rejected the email (HTTP 403)')

class NotifyTests(ReportBase):
    def setUp(self):
        super().setUp()
        self.cfg=OrgSettings.objects.get_or_create(organization=self.org)[0]
        self.cfg.admin_copy_email='boss@example.com';self.cfg.admin_copy_phone='01700000000';self.cfg.save()
        Party.objects.filter(pk=self.customer.pk).update(email='c@example.com',phone='01811111111',consent_email=True,consent_sms=True)
        self.customer.refresh_from_db()

    def send(self,key='k1',party=None):
        with self.captureOnCommitCallbacks(execute=True):
            services.notice(self.org,key,party or self.customer,'payment_recorded',{'payment_id':'P-1'})
        return NotificationOutbox.objects.get(event_key=key,recipient=party or self.customer)

    def rows(self,n):
        return {(d.channel,d.is_admin_copy):d for d in n.deliveries.all()}

    def test_one_event_makes_party_and_admin_copies_on_both_channels(self):
        with mock.patch.object(notify,'send_email',return_value=OK):n=self.send()
        r=self.rows(n)
        self.assertEqual(set(r),{('sms',False),('email',False),('sms',True),('email',True)})
        self.assertEqual((r['email',False].state,r['email',False].provider_message_id,r['email',True].to_address),('sent','m-1','boss@example.com'))
        self.assertEqual(r['sms',False].state,'failed')   # no SMS provider yet: recorded as failed, never as sent
        self.assertIn('No SMS provider',r['sms',False].last_error);self.assertEqual(n.state,'failed')

    def test_nothing_is_sent_before_commit_and_a_rollback_sends_nothing(self):
        with mock.patch.object(notify,'send_email',return_value=OK) as m:
            with self.captureOnCommitCallbacks(execute=False) as cbs:
                services.notice(self.org,'k2',self.customer,'payment_recorded',{})
            self.assertEqual(m.call_count,0);self.assertTrue(cbs)
            try:
                with transaction.atomic():
                    services.notice(self.org,'k3',self.customer,'payment_recorded',{});raise RuntimeError
            except RuntimeError:pass
            self.assertEqual(m.call_count,0);self.assertFalse(NotificationOutbox.objects.filter(event_key='k3').exists())

    def test_no_consent_or_contact_is_a_visible_skip_and_admin_copy_still_goes(self):
        Party.objects.filter(pk=self.owner.pk).update(email='o@example.com',consent_email=False,phone='',consent_sms=False)
        self.owner.refresh_from_db()
        with mock.patch.object(notify,'send_email',return_value=OK) as m:n=self.send('k4',self.owner)
        r=self.rows(n)
        self.assertEqual(r['none',False].state,'skipped_no_contact')
        self.assertEqual(r['email',True].state,'sent');self.assertEqual(m.call_count,1);self.assertEqual(m.call_args[0][0],'boss@example.com')

    def test_missing_admin_address_is_visible_not_silent(self):
        self.cfg.admin_copy_email='';self.cfg.save()
        with mock.patch.object(notify,'send_email',return_value=OK):n=self.send('k5')
        self.assertEqual(self.rows(n)['email',True].state,'skipped_no_contact')

    def test_failure_is_recorded_then_retry_sends_only_the_failed_ones(self):
        with mock.patch.object(notify,'send_email',return_value=BAD):n=self.send('k6')
        r=self.rows(n);self.assertEqual((r['email',False].state,n.state),('failed','failed'));self.assertIn('403',r['email',False].last_error)
        with mock.patch.object(notify,'send_email',return_value=OK) as m:
            with self.captureOnCommitCallbacks(execute=True):services.requeue_notice(n)
        n.refresh_from_db();r=self.rows(n)
        self.assertEqual(m.call_count,2)   # both failed emails, not a resend of anything already sent
        self.assertEqual((r['email',False].state,r['email',False].attempts),('sent',2));self.assertEqual(r['sms',False].state,'failed')
        with mock.patch.object(notify,'send_email',return_value=OK) as m:
            notify.deliver(r['email',False].pk)
        self.assertEqual(m.call_count,0)   # a sent message is never sent twice

    def test_event_key_dedupe_still_holds(self):
        with mock.patch.object(notify,'send_email',return_value=OK) as m:
            self.send('k7');self.send('k7')
        self.assertEqual(NotificationOutbox.objects.filter(event_key='k7').count(),1);self.assertEqual(m.call_count,2)

    def test_content_is_catalog_text_in_the_party_language_and_carries_no_figures(self):
        Party.objects.filter(pk=self.customer.pk).update(language='bn');self.customer.refresh_from_db()
        with mock.patch.object(notify,'send_email',return_value=OK) as m:self.send('k8')
        calls={c.args[0]:c.args for c in m.call_args_list}
        self.assertNotEqual(calls['c@example.com'][2],calls['boss@example.com'][2])
        self.assertTrue(calls['boss@example.com'][2].startswith('About '+self.customer.name))   # the admin copy is English and names the party
        for _,subject,text in calls.values():self.assertNotIn('P-1',text+subject)

    def test_sweeper_sends_what_is_still_queued(self):
        with override_settings(NOTICE_DELIVERY='off'):
            with mock.patch.object(notify,'send_email',return_value=OK) as m:
                n=self.send('k9');self.assertEqual(m.call_count,0)
                self.assertEqual(notify.sweep(older_than_seconds=-1),4)
        n.refresh_from_db();self.assertEqual(m.call_count,2);self.assertEqual(self.rows(n)['email',False].state,'sent')
