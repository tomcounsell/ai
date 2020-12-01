import logging

from apps.finance.TA import TAException, HORIZONS
from apps.finance.TA.storages.abstract.ticker import TickerStorage
from apps.signal.models import Signal

logger = logging.getLogger(__name__)

TRENDS = (BEARISH, BULLISH, OTHER) = (-1, 1, 0)


class IndicatorException(TAException):
    pass


class SignalException(TAException):
    pass


class IndicatorStorage(TickerStorage):
    """
    stores indicators in a sorted set unique to each ticker and exchange
    requires data to be a resampling to represent the most recent 5min block of time
    timestamp value must be evenly divisible by 5 minutes (300 seconds)
    add short, medium, long as 1hr, 4hr, 24hr time horizons
    """
    class_describer = "indicator"
    value_sig_figs = 6

    class_periods_list = [1,]  # class should override this
    # list of integers where for x: (1 <= x <= 200)

    requisite_pv_indexes = []  # class should override this.

    # may only include values in default_price_indexes or default_volume_indexes
    # eg. ["high_price", "low_price", "open_price", "close_price", "close_volume"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ALL INDICATORS ARE ASSUMED 5-MIN PERIOD RESAMPLED
        if self.unix_timestamp % 300 != 0:
            raise IndicatorException("indicator timestamp should be % 300")

        # self.horizon = int(kwargs.get('horizon', 1))
        self.periods = int(kwargs.get('periods', 1))  # * self.horizon))

        # if self.periods // self.horizon == 0:
        #     raise IndicatorException(f'horizon {self.horizon} '
        #                              f'must be less than periods {self.periods} (or ==1)')
        # elif self.periods % self.horizon != 0:
        #     raise IndicatorException(f'horizon {self.horizon} '
        #                              f'must be a factor of periods {self.periods}')

        self.db_key_suffix = f':{self.periods}'
        self.value = None

    def get_value(self, refresh_from_db=False):
        try:
            if self.value and not refresh_from_db:
                pass
            else:
                self.value = self.query(
                    ticker=self.ticker, exchange=self.exchange, timestamp=self.unix_timestamp
                )['values'][-1]
            if not self.value:
                self.value = self.compute_value()
        except IndexError:
            self.value = None  # value not found
        except Exception as e:
            logger.error(str(e))
            self.value = None

        return self.value

    @classmethod
    def score_from_timestamp(cls, *args, **kwargs) -> int:
        # enforce integer score == num of 5min periods since Jan_1_2017
        return int(round(super().score_from_timestamp(*args, **kwargs)))

    @classmethod
    def periods_from_seconds(cls, *args, **kwargs) -> int:
        # enforce integer periods, round to nearest
        return int(round(super().periods_from_seconds(*args, **kwargs)))

    @classmethod
    def query(cls, *args, **kwargs):

        periods_key = kwargs.get("periods_key", "")
        key_suffix = kwargs.get("key_suffix", "")

        if periods_key:
            kwargs["key_suffix"] = f'{periods_key}' + (f':{key_suffix}' if key_suffix else "")

        results_dict = super().query(*args, **kwargs)

        results_dict['periods_key'] = periods_key
        return results_dict

    @classmethod
    def get_periods_list(cls):
        periods_list = []
        for s in cls.class_periods_list:
            periods_list.extend([h * s for h in HORIZONS])
        return set(periods_list)

    def get_denoted_price_array(self, index: str = "close_price", periods: int = 0):
        from apps.finance.TA.storages.data.price import PriceStorage
        results_dict = PriceStorage.query(
            ticker=self.ticker,
            exchange=self.exchange,
            index=index,
            timestamp=self.unix_timestamp,
            periods_range=periods or self.periods
        )
        return self.get_values_array_from_query(results_dict, limit=periods)

    def compute_value(self, periods: int = 0) -> str:
        periods = periods or self.periods

        index_value_arrrays = {}
        for index in self.requisite_pv_indexes:
            index_value_arrrays[index] = self.get_denoted_price_array(index, periods)
            if not len(index_value_arrrays[index]): return ""

        return self.compute_value_with_requisite_indexes(index_value_arrrays, periods)

    def compute_value_with_requisite_indexes(self, requisite_pv_index_arrrays: dict, periods: int = 0) -> str:
        """
        custom class should set cls.requisite_pv_indexes
        override this function with custom logic

        :param index_value_arrrays: a dict with keys matching requisite+pv_indexes and values from self.get_denoted_price_array()
        :param periods: number of periods to compute value for
        :return:
        """
        # example:
        # import talib, math
        # periods = periods or self.periods
        # sma_value = talib.SMA(requisite_pv_index_arrrays["close_price"], timeperiod=periods)[-1]
        # if math.isnan(sma_value):
        #     return ""
        # return str(sma_value)
        return ""

    def compute_and_save(self) -> bool:
        """

        :return: True if value saved, else False
        """

        if not all([
            self.ticker, self.exchange, self.unix_timestamp, self.periods
        ]):
            raise Exception("missing required values")

        self.value = self.compute_value(self.periods)
        if self.value:
            self.save()
        return bool(self.value)

    @classmethod
    def compute_and_save_all_values_for_timestamp(cls, ticker, exchange, timestamp):
        new_class_storage = cls(ticker=ticker, exchange=exchange, timestamp=timestamp)
        for periods in cls.get_periods_list():
            new_class_storage.periods = periods
            new_class_storage.compute_and_save()

    def produce_signal(self):
        """
        overwrite me, defining the criteria for sending signals

        :return: None
        """
        if "this indicator" == "interesting":
            self.send_signal(trend=BULLISH)

    def send_signal(self, trend=OTHER, *args, **kwargs):
        """
        :param trend: BULLISH, BEARISH, or OTHER
        :param args:
        :param kwargs:
            add these optional kwargs:
            strength_value = 1,
            strength_max = 5,
        :return: signal object (Django model object)
        """
        from apps.finance.TA.storages.data.price import PriceStorage
        price_results_dict = PriceStorage.query(ticker=self.ticker, exchange=self.exchange)
        most_recent_price = int(price_results_dict['values'][0])
        # from apps.finance.TA.storages.data.volume import VolumeStorage
        # volume_results_dict = VolumeStorage.query(ticker=self.ticker, exchange=self.exchange)
        # most_recent_volume = float(volume_results_dict ['values'][0])

        return Signal.objects.create(
            timestamp=self.unix_timestamp,
            source=self.exchange,
            transaction_currency=self.ticker.split("_")[0],
            counter_currency=self.ticker.split("_")[1],
            resample_period=self.periods * 5,  # Signal object uses 1-min periods
            # horizon=self.horizon * 5,

            signal=self.__class__.__name__.replace("Storage", "").upper(),
            trend=trend,
            price=most_recent_price,
            **kwargs
        )

    def save(self, *args, **kwargs):

        # check meets basic requirements for saving
        if not all([self.ticker, self.exchange,
                    self.periods, self.value,
                    self.unix_timestamp]):
            logger.error("incomplete information, cannot save \n" + str(self.__dict__))
            raise IndicatorException("save error, missing data")

        self.db_key_suffix = f'{str(self.periods)}'
        save_result = super().save(*args, **kwargs)
        try:
            self.produce_signal()
        except Exception as e:
            logger.error("error producing signal for indicator" + str(e))
        return save_result


"""
===== EXAMPLE USAGE =====

my_indicator = TimeseriesIndicator(ticker="ETH_BTC",
                                   exchange="bittrex",
                                   timestamp=1483228800,
                                   periods=12*20)
my_indicator.value = "BUY BITCOIN"
my_indicator.save()

# advanced:

very_special_signal = TimeseriesIndicator(class_describer="SuperSignal",
                                          key="SuperSignal",
                                          key_suffix="answer_to_the_universe",
                                          ticker="ETH_BTC",
                                          timestamp=1483228800
                                          )
from settings.redis_db import database
pipeline = database.pipeline()
for thing in ['towel', 42, 'babelfish', 'vogon poetry']:
    very_special_signal.unix_timestamp += 300
    very_special_signal.value = thing
    pipeline = very_special_signal.save(pipeline)
pipeline.execute()

===== EXAMPLE USAGE =====
"""
