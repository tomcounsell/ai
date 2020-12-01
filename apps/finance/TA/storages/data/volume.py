import logging

from apps.finance.TA import TAException
from apps.finance.TA.storages.abstract.ticker import TickerStorage
from apps.finance.TA.storages.abstract.ticker_subscriber import TickerSubscriber, timestamp_is_near_5min, \
    get_nearest_5min_timestamp
from apps.finance.TA.storages.data.pv_history import PriceVolumeHistoryStorage, default_volume_indexes, derived_volume_indexes

logger = logging.getLogger(__name__)


class VolumeException(TAException):
    pass


class VolumeStorage(TickerStorage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.index = kwargs.get('index', "close_volume")
        self.value = kwargs.get('value')

    def save(self, *args, **kwargs):

        # meets basic requirements for saving
        if not all([self.ticker, self.exchange,
                   self.index, self.value,
                   self.unix_timestamp]):
            logger.error("incomplete information, cannot save \n" + str(self.__dict__))
            raise VolumeException("save error, missing data")

        if not self.force_save:
            if not self.index in default_volume_indexes + derived_volume_indexes:
                logger.error("volume index not in approved list, raising exception...")
                raise VolumeException("unknown index")

        self.db_key_suffix = f':{self.index}'
        return super().save(*args, **kwargs)


class VolumeSubscriber(TickerSubscriber):

    classes_subscribing_to = [
        PriceVolumeHistoryStorage
    ]

    def handle(self, channel, data, *args, **kwargs):

        # parse timestamp from data
        # f'{data_history.ticker}:{data_history.exchange}:{data_history.timestamp}'
        [ticker, exchange, timestamp] = data.split(":")

        if not timestamp_is_near_5min(timestamp):
            return

        # logger.debug("near to a 5 min time marker")
        timestamp = get_nearest_5min_timestamp(timestamp)

        volume = VolumeStorage(ticker=ticker, exchange=exchange, timestamp=timestamp)
        index_values = {}

        for index in default_volume_indexes:
            logger.debug(f'process volume for ticker: {ticker}')

            # example key = "XPM_BTC:poloniex:PriceVolumeHistoryStorage:close_price"
            sorted_set_key = f'{ticker}:{exchange}:PriceVolumeHistoryStorage:{index}'

            index_values[index] = [
                float(db_value.decode("utf-8").split(":")[0])
                for db_value
                in self.database.zrangebyscore(sorted_set_key, timestamp - 300, timestamp + 45) # todo: update to scores
            ]

            try:
                if not len(index_values[index]):
                    volume.value = None

                elif index == "close_volume":
                    volume.value = index_values["close_volume"][-1]


            except IndexError:
                pass  # couldn't find a useful value
            except ValueError:
                pass  # couldn't find a useful value
            else:
                if volume.value:
                    volume.index = index
                    volume.save()
                    # logger.info("saved new thing: " + volume.get_db_key())

            # all_values_set = (
            #         set(index_values["open_volume"])
            # )
            #
            # if not len(all_values_set):
            #     return
            #
            # for index in derived_volume_indexes:
            #     volume.value = None
            #     values_set = all_values_set.copy()
            #
            #     if index == "open_volume":
            #         volume.value = index_values["open_volume"][0]
            #
            #     elif index == "low_volume":
            #         volume.value = min(index_values["low_volume"])
            #     elif index == "high_volume":
            #         volume.value = max(index_values["high_volume"])
            #
            #     if volume.value:
            #         volume.index = index
            #         volume.save(publish=True)
