"""Builder 1 handoff: call URLs must serve the voice pipeline's TwiML."""

from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from uuid import uuid4


def with_query(url, **values):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(values)
    return urlunsplit(parts._replace(query=urlencode(query)))


class Rejected(Exception):
    """Provider definitively rejected the request; no resource was created."""

    def __init__(self, message="Provider rejected request", *, code=None, http_status=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status

    def details(self):
        # Do not persist provider messages, which may contain personal data.
        return {"code": self.code, "http_status": self.http_status}


class MockProvider:
    def __init__(self):
        self.calls = []
        self.messages = []

    def start_call(self, phone, call_id):
        sid = "mock_call_" + uuid4().hex
        self.calls.append({"id": sid, "phone": phone, "call_id": call_id})
        return sid

    def send_sms(self, phone, body, alert_id):
        sid = "mock_sms_" + uuid4().hex
        self.messages.append({"id": sid, "phone": phone, "body": body, "alert_id": alert_id})
        return sid


class TwilioProvider:
    def __init__(self, account_sid, auth_token, from_number, public_base_url, voice_url):
        from twilio.rest import Client
        from twilio.http.http_client import TwilioHttpClient

        # Disable transport retries: a timed-out create may already have succeeded.
        self.client = Client(account_sid, auth_token,
                             http_client=TwilioHttpClient(timeout=15, max_retries=0))
        self.from_number = from_number
        self.base_url = public_base_url.rstrip("/")
        self.voice_url = voice_url

    @staticmethod
    def _create(method, **kwargs):
        from twilio.base.exceptions import TwilioRestException

        try:
            return method(**kwargs).sid
        except TwilioRestException as exc:
            if 400 <= exc.status < 500:
                raise Rejected(code=exc.code, http_status=exc.status) from exc
            raise

    def start_call(self, phone, call_id):
        return self._create(
            self.client.calls.create, to=phone, from_=self.from_number,
            url=with_query(self.voice_url, call_id=call_id), timeout=30,
            status_callback=with_query(self.base_url + "/webhooks/twilio/calls", call_id=call_id),
            status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )

    def send_sms(self, phone, body, alert_id):
        return self._create(
            self.client.messages.create, to=phone, from_=self.from_number, body=body,
            status_callback=with_query(self.base_url + "/webhooks/twilio/messages", alert_id=alert_id),
        )
