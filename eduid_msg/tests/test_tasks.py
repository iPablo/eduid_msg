from mock import patch, call, MagicMock
import json
from eduid_msg.tests import MongoTestCase
from eduid_msg.celery import celery
from eduid_msg.tasks import send_message, is_reachable
from eduid_msg.utils import load_template
import pkg_resources


class TestTasks(MongoTestCase):
    def setUp(self):
        super(TestTasks, self).setUp()
        data_dir = pkg_resources.resource_filename(__name__, 'data')
        settings = {
            'BROKER_TRANSPORT': 'memory',
            'BROKER_URL': 'memory://',
            'CELERY_EAGER_PROPAGATES_EXCEPTIONS': True,
            'CELERY_ALWAYS_EAGER': True,
            'CELERY_RESULT_BACKEND': "cache",
            'CELERY_CACHE_BACKEND': 'memory',
            'MONGO_URI': 'mongodb://localhost:%d/' % self.port,
            'MONGO_DBNAME': 'test',
            'SMS_ACC': 'foo',
            'SMS_KEY': 'bar',
            'SMS_SENDER': 'Test sender',
            'MM_DEFAULT_SUBJECT': 'Test case',
            'MM_SENDER_ORG_NR': '1234567',
            'TEMPLATE_DIR': data_dir,
            'MESSAGE_RATE_LIMIT': '2/m',
        }
        celery.conf.update(settings)
        self.msg_dict = {
            'name': 'Godiskungen',
            'admin': 'Testadmin'
        }

        class APIResponse(MagicMock):
            status_code = 200

            def json(self):
                return self.data

        self.response = APIResponse

        self.recipient_ok = {
            u'SenderAccepted': True,
            u'AccountStatus': {
                u'ServiceSupplier': {
                    u'Id': u'162021005448',
                    u'ServiceAddress': u'x',
                    u'Name': u'x'
                },
                u'Type': u'Secure',
                u'RecipientId': u'192705178354',
                u'Pending': False
            }
        }

        self.recipient_sender_not = {
            u'SenderAccepted': False,
            u'AccountStatus': {
                u'ServiceSupplier': {
                    u'Id': u'162021005448',
                    u'ServiceAddress': u'x',
                    u'Name': u'x'
                },
                u'Type': u'Secure',
                u'RecipientId': u'192705178354',
                u'Pending': False
            }
        }

        self.recipient_not = {
            u'SenderAccepted': False,
            u'AccountStatus': {
                u'ServiceSupplier': {
                    u'Id': u'162021005448',
                    u'ServiceAddress': u'x',
                    u'Name': u'x'
                },
                u'Type': u'Not',
                u'RecipientId': u'192705178354',
                u'Pending': False
            }
        }

        self.recipient_anon = {
            u'SenderAccepted': True,
            u'AccountStatus': {
                u'ServiceSupplier': {
                    u'Id': u'162021005448',
                    u'ServiceAddress': u'x',
                    u'Name': u'x'
                },
                u'Type': u'Anonymous',
                u'RecipientId': u'192705178354',
                u'Pending': False
            }
        }


    @patch('smscom.SMSClient.send')
    def test_send_message_sms(self, sms_mock):
        sms_mock.return_value = True
        status = send_message.delay('sms', self.msg_dict, '+466666', 'test.tmpl', 'sv_SE').get()

        # Test that the template was actually used in send_message function call to the sms service
        template = load_template(celery.conf.get('TEMPLATE_DIR'), 'test.tmpl', self.msg_dict, 'sv_SE')
        expected = [call(template, 'Test sender', '+466666', prio=2)]
        self.assertEqual(sms_mock.mock_calls, expected)
        self.assertTrue(status)

    def test_send_message_invalid_phone_number(self):
        try:
            send_message.delay('sms', self.msg_dict, '+466666a', 'test.tmpl', 'sv_SE').get()
        except ValueError, e:
            self.assertEqual(e.message, "'to' is not a valid phone number")

    @patch('eduid_msg.tasks.MessageRelay.mm_api')
    def test_is_reachable_cache(self, api_mock):
        response = self.response()
        response.data = self.recipient_ok
        api_mock.user.reachable.POST.return_value = response
        status = is_reachable.delay('192705178354').get()
        self.assertTrue(status)
        mdb = self.conn['test']
        result = mdb['recipient_cache'].find_one({'identifier': '192705178354'})
        self.assertEqual(result['data']['AccountStatus']['ServiceSupplier']['Id'], '162021005448')

    @patch('eduid_msg.tasks.MessageRelay.mm_api')
    def test_send_message_mm(self, api_mock):
        reachable_response = self.response()
        reachable_response.data = self.recipient_ok
        api_mock.user.reachable.POST.return_value = reachable_response
        message_response = self.response()
        message_response.data = {'transaction_id': 'transaction_id'}
        api_mock.message.send.POST.return_value = message_response
        recipient = '192705178354'
        send_message.delay('mm', self.msg_dict, recipient, 'test.tmpl', 'sv_SE', subject='Test').get()

        # Test that the template was actually used in send_message function call to the mm service
        template = load_template(celery.conf.get('TEMPLATE_DIR'), 'test.tmpl', self.msg_dict, 'sv_SE')
        reachable_data = json.dumps({"identity_number": recipient})
        message_data = json.dumps({"message": template, "recipient": recipient, "content_type": "text/html",
                                   "language": "svSE", "subject": "Test"})
        expected = [call.user.reachable.POST(data=reachable_data), call.message.send.POST(data=message_data)]
        self.assertEqual(api_mock.mock_calls, expected)

    @patch('eduid_msg.tasks.MessageRelay.mm_api')
    def test_send_message_mm_sender_not_accepted(self, api_mock):
        reachable_response = self.response()
        reachable_response.data = self.recipient_sender_not
        api_mock.user.reachable.POST.return_value = reachable_response
        message_response = self.response()
        message_response.data = {'transaction_id': 'transaction_id'}
        api_mock.message.send.POST.return_value = message_response
        status = send_message.delay('mm', self.msg_dict, '192705178354', 'test.tmpl', 'sv_SE', subject='Test').get()
        self.assertEqual(status, "Sender_not")

    @patch('eduid_msg.tasks.MessageRelay.mm_api')
    def test_send_message_mm_recipient_not_existing(self, api_mock):
        reachable_response = self.response()
        reachable_response.data = self.recipient_not
        api_mock.user.reachable.POST.return_value = reachable_response
        status = send_message.delay('mm', self.msg_dict, '192705178354', 'test.tmpl', 'sv_SE', subject='Test').get()
        self.assertEqual(status, False)

    @patch('eduid_msg.tasks.MessageRelay.mm_api')
    def test_send_message_mm_recipient_anonymous(self, api_mock):
        reachable_response = self.response()
        reachable_response.data = self.recipient_anon
        api_mock.user.reachable.POST.return_value = reachable_response
        status = send_message.delay('mm', self.msg_dict, '192705178354', 'test.tmpl', 'sv_SE', subject='Test').get()
        self.assertEqual(status, "Anonymous")
