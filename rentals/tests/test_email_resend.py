import io
import json
import urllib.error
from unittest import mock
from django.test import SimpleTestCase, override_settings
from ..services import email_resend as er

CONF = dict(RESEND_API_KEY='k', EMAIL_FROM_ADDRESS='a@b.com', EMAIL_FROM_NAME='RentFlow', EMAIL_TIMEOUT=5)

class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass

class ResendTests(SimpleTestCase):
    @override_settings(RESEND_API_KEY='', EMAIL_FROM_ADDRESS='')
    def test_unconfigured_never_reports_sent(self):
        r = er.send_email('x@y.com', 's', 't')
        self.assertFalse(r.sent); self.assertEqual(r.provider, 'console')

    @override_settings(**CONF)
    def test_success_sends_expected_request(self):
        with mock.patch('urllib.request.urlopen', return_value=Resp(b'{"id":"m1"}')) as op:
            r = er.send_email('x@y.com', 'Sub', 'Body')
        self.assertTrue(r.sent); self.assertEqual(r.message_id, 'm1')
        req = op.call_args[0][0]
        self.assertEqual(req.get_header('Authorization'), 'Bearer k')
        sent = json.loads(req.data)
        self.assertEqual(sent['to'], ['x@y.com']); self.assertEqual(sent['from'], 'RentFlow <a@b.com>')

    @override_settings(**CONF)
    def test_http_error_is_returned_not_raised(self):
        err = urllib.error.HTTPError(er.RESEND_URL, 403, 'no', {}, io.BytesIO(b'{"message":"domain not verified"}'))
        with mock.patch('urllib.request.urlopen', side_effect=err):
            r = er.send_email('x@y.com', 's', 't')
        self.assertFalse(r.sent); self.assertIn('403', r.error)

    @override_settings(**CONF)
    def test_network_error_is_returned_not_raised(self):
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('down')):
            self.assertFalse(er.send_email('x@y.com', 's', 't').sent)
