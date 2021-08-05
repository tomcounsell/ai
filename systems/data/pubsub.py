from abc import ABC
import logging

import msgpack
import msgpack_numpy as m

m.patch()
logger = logging.getLogger(__name__)
from settings.redis_db import redis_db

class PublisherException(Exception):
    pass


class Publisher(ABC):
    def __init__(self, *args, **kwargs):
        self.publish_data = None

    def publish(self, channel_name="", publish_data=None, *args, **kwargs):
        # logger.debug(f"publish to {channel_name}: {publish_data}")
        channel_name = channel_name or self.__class__.__name__
        publish_data = publish_data or self.publish_data
        if not publish_data:
            return
        elif not channel_name:
            raise PublisherException("missing channel to publish to")
        # do some transformations here?
        subscriber_count = redis_db.publish(channel_name, msgpack.dumps(publish_data))
        logger.debug(f"published data to `{channel_name}`, {subscriber_count} subscribers")


class SubscriberException(Exception):
    pass


class Subscriber(ABC):
    class_describer = "subscriber"
    classes_subscribing_to = [
        # ...
    ]

    def __init__(self):
        self.pubsub = redis_db.pubsub()
        logger.info(f'New pubsub for {self.__class__.__name__}')
        for s_class in self.classes_subscribing_to:
            self.pubsub.subscribe(s_class if isinstance(s_class, str) else s_class.__name__)
            logger.info(f'{self.__class__.__name__} subscribed to '
                        f'{s_class if isinstance(s_class, str) else s_class.__name__} channel')

    def __call__(self):
        data_event = self.pubsub.get_message()
        if not data_event:
            return
        if not data_event.get('type') == 'message':
            return

        # logger.debug(f'got message: {data_event}')

        # data_event = {
        #   'type': 'message',
        #   'pattern': None,
        #   'channel': b'PriceStorage',
        #   'data': b'{
        #       "key": f'{self.ticker}:{self.publisher}:PriceStorage:{periods}',
        #       "name": "9545225909:1533883300",
        #       "score": "1533883300"
        #   }'
        # }

        try:
            channel_name = data_event.get('channel').decode("utf-8")
            event_data = msgpack.loads(data_event.get('data'))
            logger.debug(f'handling event in {self.__class__.__name__}')
            self.pre_handle(channel_name, event_data)
            self.handle(channel_name, event_data)
        except KeyError as e:
            logger.warning(f'unexpected format: {data_event} ' + str(e))
            pass  # message not in expected format, just ignore
        except msgpack.exceptions.FormatError:
            logger.warning(f'unexpected data format: {data_event["data"]}')
            pass  # message not in expected format, just ignore
        except Exception as e:
            raise SubscriberException(f'Error calling {self.__class__.__name__}: ' + str(e))

    def pre_handle(self, channel, data, *args, **kwargs):
        pass

    def handle(self, channel, data, *args, **kwargs):
        """
        overwrite me with some logic
        :return: None
        """
        logger.warning(f'NEW MESSAGE for '
                       f'{self.__class__.__name__} subscribed to '
                       f'{channel} channel '
                       f'BUT HANDLER NOT DEFINED! '
                       f'... message/event discarded')
        pass
