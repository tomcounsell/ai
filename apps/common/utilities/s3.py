import boto
from settings import AWS_OPTIONS, DEFAULT_FILE_STORAGE
import logging
logger = logging.getLogger(__name__)

def get_readable_stream(s3_key):
    s3_key.open_read()
    return s3_key.read()


def get_s3_key(bucket_name: str, key_name: str):
    # returns boto.s3.key
    return boto.connect_s3().get_bucket(bucket_name).get_key(key_name)


def get_s3_readable_key(bucket_name: str, key_name: str):
    return get_readable_stream(get_s3_key(bucket_name, key_name))


def create_public_url(s3_key) -> str:
    s3_key.set_acl('public-read')
    return s3_key.generate_url(expires_in=0, query_auth=False)

# added by @AlexY for downloading a keras model
def download_file_from_s3(s3filename):
    conn = boto.s3.connect_to_region("us-east-1",
                                     aws_access_key_id=AWS_OPTIONS['AWS_ACCESS_KEY_ID'],
                                     aws_secret_access_key=AWS_OPTIONS['AWS_SECRET_ACCESS_KEY'])

    bucket = conn.get_bucket(AWS_OPTIONS['AWS_STORAGE_BUCKET_NAME'])
    key_obj = boto.s3.key.Key(bucket)
    key_obj.key = s3filename

    contents = key_obj.get_contents_to_filename(s3filename)
