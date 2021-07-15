from django.utils import timezone
import hashlib
import hmac
import json
from datetime import timedelta

from django.contrib import messages

from settings.vendor_services_settings import TRANSLOADIT_OPTIONS

# 'Constants'
DEFAULT_SIGNATURE_EXPIRATION_MINUTES = 70




def check_transloadit_upload_response(request):
  if 'transloadit' not in request.POST:
    messages.warning(request, "Photo upload failed. Please try again.")
    raise RuntimeError("Photo upload failed. Please try again.")

  raw_data = request.POST['transloadit']
  upload_json = json.loads(raw_data)  # type: json

  if upload_json['ok'] != 'ASSEMBLY_COMPLETED':
    raise RuntimeError("Photo upload failed. Error code: " + upload_json['error'])


def prepare_upload_info(context, template_id):
  """
  Handles the boilerplate of preparing the payload and signature
  :param context: For template
  :param template_id: transloadit template id
  :return:
  """
  payload = create_payload(template_id)
  sig = create_signature(payload)
  context = append_upload_template_info(context,payload,sig)
  return context


def append_upload_template_info(context, payload, signature):
  """
  Appends transloadit info to an existing context.
  Allows proper use of the upload template
  :param signature:
  :param payload:
  :param context: django.template.Context
  """
  context['signature'] = signature
  context['params'] = json.dumps(payload,separators=(',',':'))
  return context


def create_payload(template_id):
  """
  :return: payload value corresponding to 'params' key in transloadit js blob
  """
  ex_key, ex_val = create_expires_tag()
  payload = {
    'auth': {
      'key': TRANSLOADIT_OPTIONS['TRANSLOADIT_AUTH_KEY'],
      ex_key: ex_val
      },
    'template_id':template_id
  }
  return payload


def create_signature(payload):
  """
  :param payload: dictionary containing "auth" and template_id params
  :return: signature for use in transloadit form
  """
  assert 'auth' in payload
  json_dump = json.dumps(payload,separators=(',',':'))
  return hmac.new(TRANSLOADIT_OPTIONS['TRANSLOADIT_SECRET_KEY'],
                  json_dump,
                  hashlib.sha1).hexdigest()


def create_expires_tag():
  """
  Returns a 2-tuple, key value pair for expires
  :return:
  """
  return 'expires', create_expires_value(timezone.now())


def create_expires_value(timepoint,
                         expiration_in_minutes=DEFAULT_SIGNATURE_EXPIRATION_MINUTES):
  return (timepoint + timedelta(minutes=expiration_in_minutes)).strftime('%Y/%m/%d %H:%M:%S')
