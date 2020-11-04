import boto
from settings import AWS_OPTIONS, DEFAULT_FILE_STORAGE
from boto.sqs.message import Message
import json
from settings import QUEUE_NAME, AWS_OPTIONS, BETA_QUEUE_NAME, TEST_QUEUE_NAME

import logging
logger = logging.getLogger(__name__)


def send_sqs(dictionary):
    message = Message()
    message.set_body(json.dumps(dictionary))

    sqs_connection = boto.sqs.connect_to_region("us-east-1",
                                                aws_access_key_id=AWS_OPTIONS['AWS_ACCESS_KEY_ID'],
                                                aws_secret_access_key=AWS_OPTIONS['AWS_SECRET_ACCESS_KEY'])

    if QUEUE_NAME:
        logging.debug("emitted to QUEUE_NAME queue :" + QUEUE_NAME)
        production_queue = sqs_connection.get_queue(QUEUE_NAME)
        production_queue.write(message)

    if BETA_QUEUE_NAME:
        logging.debug("emitted to BETA_QUEUE_NAME queue :" + BETA_QUEUE_NAME)
        test_queue = sqs_connection.get_queue(BETA_QUEUE_NAME)
        test_queue.write(message)

    if TEST_QUEUE_NAME:
        logging.debug("emitted to TEST_QUEUE_NAME queue :" + TEST_QUEUE_NAME)
        test_queue = sqs_connection.get_queue(TEST_QUEUE_NAME)
        test_queue.write(message)

    logger.info("EMITTED SIGNAL: " + str(dictionary))