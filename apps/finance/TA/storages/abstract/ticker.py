import logging
from apps.finance.TA import TAException
from apps.finance.TA.storages.abstract.timeseries_storage import TimeseriesStorage
from settings import EXCHANGE_MARKETS

logger = logging.getLogger(__name__)


class IndicatorException(TAException):
    pass


class TickerStorage(TimeseriesStorage):
    """
    stores timeseries data on tickers in a sorted set unique to each ticker and exchange
    todo: split the db by each exchange source
    """
    class_describer = "ticker"
    value_sig_figs = 6

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 'ticker' REQUIRED
        # 'exchange EXPECTED BUT CAN STILL SAVE WITHOUT
        try:
            self.ticker = str(kwargs['ticker'])  # str eg. BTC_USD
            self.exchange = str(kwargs['exchange'])  # str eg. binance
        except KeyError:
            raise TAException("Indicator requires a ticker and exchange as parameters")
        except Exception as e:
            raise TAException(str(e))
        else:
            if self.ticker.find("_") <= 0:
                raise TAException("ticker should be like BTC_USD")
            if self.exchange not in EXCHANGE_MARKETS:
                logger.debug("----- UNKNOWN EXCHANGE! ARE YOU SURE? -----")


    def get_db_key(self):
        self.db_key_prefix = f'{self.ticker}:{self.exchange}:'
        # by default will return "{ticker}:{exchange}:{class_name}"
        return super().get_db_key()


    @classmethod
    def query(cls, *args, **kwargs):

        ticker = kwargs.get("ticker", None)
        if not ticker:
            raise IndicatorException("ticker required for ticker query")
        exchange = kwargs.get("exchange", "CMC")
        kwargs["key_prefix"] = f'{ticker}:{exchange}'

        results_dict = super().query(*args, **kwargs)
        if results_dict:
            results_dict['exchange'] = exchange
            results_dict['ticker'] = ticker
        return results_dict
