import logging

from apps.finance.TA import TAException
from apps.finance.TA.storages.abstract.ticker import TickerStorage
from apps.finance.TA.storages.abstract.ticker_subscriber import TickerSubscriber, score_is_near_5min
from apps.finance.TA.storages.data.pv_history import default_price_indexes, derived_price_indexes, PriceVolumeHistoryStorage
from apps.finance.TA.storages.utils.memory_cleaner import clear_pv_history_values

logger = logging.getLogger(__name__)


class PriceException(TAException):
    pass


class PriceStorage(TickerStorage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.index = kwargs.get('index', "close_price")
        self.value = kwargs.get('value')
        self.db_key_suffix = f':{self.index}'  # redundant?

    def save(self, *args, **kwargs):

        # meets basic requirements for saving
        if not all([self.ticker, self.exchange,
                    self.index, self.value,
                    self.unix_timestamp]):
            logger.error("incomplete information, cannot save \n" + str(self.__dict__))
            raise PriceException("save error, missing data")

        if not self.force_save:
            if not self.index in default_price_indexes + derived_price_indexes:
                logger.error("price index not in approved list, raising exception...")
                raise PriceException("unknown index")

        if self.unix_timestamp % 300 != 0:
            raise PriceException("price timestamp should be % 300")

        self.db_key_suffix = f':{self.index}'
        return super().save(*args, **kwargs)

    @classmethod
    def query(cls, *args, **kwargs):

        if kwargs.get("periods_key", None):
            raise PriceException("periods_key is not usable in PriceStorage query")

        key_suffix = kwargs.get("key_suffix", "")
        index = kwargs.get("index", "close_price")
        kwargs["key_suffix"] = f'{index}' + (f':{key_suffix}' if key_suffix else "")

        results_dict = super().query(*args, **kwargs)

        results_dict['index'] = index
        return results_dict


class PriceSubscriber(TickerSubscriber):
    classes_subscribing_to = [
        PriceVolumeHistoryStorage
    ]

    def handle(self, channel, data, *args, **kwargs):
        from apps.finance.TA.storages.utils.pv_resampling import generate_pv_storages # import here, bc has circular dependancy

        # parse data like...
        # {
        #     "key": "POLY_BTC:binance:PriceVolumeHistoryStorage:open_price",
        #     "name": "3665:176255.873",
        #     "score": "176255.873"
        # }

        # eg. sorted_set_key = data["key"]

        [ticker, exchange, object_class, index] = data["key"].split(":")
        if not object_class == channel == PriceVolumeHistoryStorage.__name__:
            logger.warning(f'Unexpected that these are not the same:'
                           f'object_class: {object_class}, '
                           f'channel: {channel}, '
                           f'subscribing class: {PriceVolumeHistoryStorage.__name__}')
        [value, name_score] = data["name"].split(":")

        score = float(data["score"])

        if not float(name_score) == float(data["score"]):
            logger.warning(f'Unexpected that score in name {name_score}'
                           f'is different than score {score}')

        if score_is_near_5min(score):
            if generate_pv_storages(ticker, exchange, index, score):
                if index == "close_price":
                    clear_pv_history_values(ticker, exchange, score)
