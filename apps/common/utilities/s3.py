import boto3
from settings import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME
import logging
logger = logging.getLogger(__name__)


def download_s3_file_to_local(s3_filename, local_filename):

    # s3 = boto3.resource('s3', aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
    # bucket = s3.Bucket(AWS_S3_BUCKET_NAME)
    # obj = bucket.Object(s3_filename)
    # with open(local_filename, 'wb') as data:
    #     obj.download_fileobj(data)

    s3_client = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)

    s3_filename = s3_filename[s3_filename.find("ML_model_"):]
    logging.debug(f"downloading s3 key: {s3_filename} from bucket: {AWS_S3_BUCKET_NAME}")
    s3_client.download_file(AWS_S3_BUCKET_NAME, s3_filename, local_filename)
    logging.debug(f"file saved to: {local_filename}")
    return True
