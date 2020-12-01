import json
from abc import ABC
import logging
from json import JSONDecodeError

from apps.finance.TA import TAException

logger = logging.getLogger(__name__)


class SubscriberException(TAException):
    pass


class TickerSubscriber(ABC):
    class_describer = "ticker_subscriber"
    classes_subscribing_to = [
        # ...
    ]

    def __init__(self):
        from settings.redis_db import database
        self.database = database
        self.pubsub = database.pubsub()
        logger.info(f'New pubsub for {self.__class__.__name__}')
        for s_class in self.classes_subscribing_to:
            self.pubsub.subscribe(s_class.__name__)
            logger.info(f'{self.__class__.__name__} subscribed to '
                        f'{s_class.__name__} channel')

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
        #       "key": f'{self.ticker}:{self.exchange}:PriceStorage:{periods}',
        #       "name": "9545225909:1533883300",
        #       "score": "1533883300"
        #   }'
        # }

        try:
            channel_name = data_event.get('channel').decode("utf-8")
            event_data = json.loads(data_event.get('data').decode("utf-8"))
            # logger.debug(f'handling event in {self.__class__.__name__}')
            self.pre_handle(channel_name, event_data)
            self.handle(channel_name, event_data)
        except KeyError as  e:
            logger.warning(f'unexpected format: {data_event} ' + str(e))
            pass  # message not in expected format, just ignore
        except JSONDecodeError:
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


def timestamp_is_near_5min(timestamp) -> bool:
    # close to a five minute period mark? (+ or - 45 seconds)
    seconds_from_five_min = (int(timestamp) + 45) % 300
    return bool(seconds_from_five_min < 90)

def score_is_near_5min(score) -> bool:
    return bool(round(score) - 45/300 < score < round(score) + 45/300)

def get_nearest_5min_timestamp(timestamp) -> int:
    return ((int(timestamp) + 45) // 300) * 300

def get_nearest_5min_score(score) -> int:
    return int(round(score))
