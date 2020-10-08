import boto3
from settings import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME
import logging
logger = logging.getLogger(__name__)


def download_s3_file_to_local(s3_filename, local_filename):

    s3 = boto3.resource('s3', aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
    bucket = s3.Bucket(AWS_S3_BUCKET_NAME)
    obj = bucket.Object(s3_filename)

    with open(local_filename, 'wb') as data:
        obj.download_fileobj(data)

    return True
