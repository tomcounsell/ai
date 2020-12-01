from apps.finance.TA.storages.abstract.indicator import IndicatorException, IndicatorStorage
from apps.finance.TA.storages.abstract.ticker_subscriber import TickerSubscriber, get_nearest_5min_timestamp
from settings import logger


class IndicatorSubscriber(TickerSubscriber):
    class_describer = "indicator_subscriber"
    classes_subscribing_to = [
        # ...
    ]
    storage_class = IndicatorStorage  # override with applicable storage class

    def extract_params(self, channel, data, *args, **kwargs):

        # parse data like...
        # {
        #     "key": "VEN_USDT:binance:PriceStorage:close_price",
        #     "name": "176760000:1532373300",
        #     "score": "1532373300"
        # }

        [self.ticker, self.exchange, object_class, self.key_suffix] = data["key"].split(":")

        if not object_class == channel and object_class in [
            sub_class.__name__ for sub_class in self.classes_subscribing_to
        ]:
            logger.warning(f'Unexpected that these are not the same:'
                           f'object_class: {object_class}, '
                           f'channel: {channel}, '
                           f'subscribing classes: {self.classes_subscribing_to}')

        [value, score] = data["name"].split(":")
        self.value, self.score = float(value), float(score)

        if not self.score == int(float(data["score"])):
            logger.warning(f'Unexpected that score in name {self.score} '
                           f'is different than score {data["score"]}')

        if not self.score == int(self.score):
            raise IndicatorException("indicator timestamp should be 5min timestamp, score should be whole number (int)")

        self.timestamp = IndicatorStorage.timestamp_from_score(self.score)

        return


    def pre_handle(self, channel, data, *args, **kwargs):
        self.extract_params(channel, data, *args, **kwargs)
        super().pre_handle(channel, data, *args, **kwargs)


    def handle(self, channel, data, *args, **kwargs):
        """
        a new instance was saved belonging to one of the classes subscribed to
        for standard indicators self.ticker, self.exchange, and self.timestamp are available
        these values were collected by self.pre_handle() and self.extract_params()

        :param channel:
        :param data:
        :param args:
        :param kwargs:
        :return:
        """

        if self.key_suffix not in self.storage_class.requisite_pv_indexes:
            logger.debug(f'index {self.key_suffix} is not in {self.storage_class.requisite_pv_indexes} ...ignoring...')
            return

        self.storage_class.compute_and_save_all_values_for_timestamp(self.ticker, self.exchange, self.timestamp)
