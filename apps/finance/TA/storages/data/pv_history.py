import logging

from apps.finance.TA import TAException
from apps.finance.TA.storages.abstract.indicator import TickerStorage

logger = logging.getLogger(__name__)


default_price_indexes = ["high_price", "low_price", "open_price", "close_price", ]
derived_price_indexes = ["midpoint_price", "mean_price", "price_variance",]
default_volume_indexes = ["close_volume",]
derived_volume_indexes = ["open_volume", "high_volume", "low_volume",]
default_indexes = default_price_indexes + default_volume_indexes
derived_indexes = derived_price_indexes + derived_volume_indexes
all_indexes = default_indexes + derived_indexes

class PriceVolumeHistoryException(TAException):
    pass


class PriceVolumeHistoryStorage(TickerStorage):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.index = kwargs.get('index', "close_price")
        self.value = kwargs.get('value')


    @classmethod
    def query(cls, *args, **kwargs):

        # "ETH_BTC:poloniex:PriceVolumeHistoryStorage:close_price"
        # f'{ticker}:{exchange}:{cls.__name__}:{index}'

        if not "index" in kwargs:
            logger.debug("assuming to use `close_price` index in price query")
        index = kwargs["index"] = kwargs.get("index", "close_price")

        if kwargs.get("key_suffix", None):
            logger.warning("`key_suffix` has been removed from your query. "
                           "it cannot be used in query for PriceVolumeHistoryStorage. "
                           "Only an `index` can be specified and if not provided, "
                           "it defaults to 'close_price'")

        kwargs["key_suffix"] = f':{index}'

        results_dict = super().query(*args, **kwargs)

        if results_dict:
            results_dict['index'] = index
        return results_dict


    @classmethod
    def destroy(cls, *args, **kwargs):
        pass


    def save(self, *args, **kwargs):

        # meets basic requirements for saving
        if not all([self.ticker, self.exchange,
                   self.index, self.value,
                   self.unix_timestamp]):
            logger.error("incomplete information, cannot save \n" + str(self.__dict__))
            raise PriceVolumeHistoryException("save error, missing data")

        if not self.force_save:
            if not self.index in default_price_indexes + default_volume_indexes:
                logger.error("price index not in approved list, raising exception...")
                raise PriceVolumeHistoryException("unknown index: " + str(self.index))

        self.db_key_suffix = f':{self.index}'
        # logger.debug("ready to save, db_key will be " + self.get_db_key())
        return super().save(*args, **kwargs)
