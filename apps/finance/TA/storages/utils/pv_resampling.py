import logging

from apps.finance.TA import PRICE_INDEXES, VOLUME_INDEXES
from apps.finance.TA.storages.abstract.ticker_subscriber import get_nearest_5min_score
from apps.finance.TA.storages.abstract.timeseries_storage import TimeseriesStorage
from apps.finance.TA.storages.data.price import PriceStorage
from apps.finance.TA.storages.data.pv_history import PriceVolumeHistoryStorage, derived_price_indexes
from apps.finance.TA.storages.data.volume import VolumeStorage

logger = logging.getLogger(__name__)


def generate_pv_storages(ticker: str, exchange: str, index: str, score: float) -> bool:
    """
    resample values from PriceVolumeHistoryStorage into 5min periods in PriceStorage and VolumeStorage
    :param ticker: eg. "ETH_BTC"
    :param exchange: eg. "binance"
    :param index: eg. "close_price"
    :param score: as defined by TimeseriesStorage.score_from_timestamp()
    :return: True if successful at generating a new storage index value for the score, else False
    """
    score = get_nearest_5min_score(score)
    timestamp = TimeseriesStorage.timestamp_from_score(score)

    if index in PRICE_INDEXES:
        storage = PriceStorage(ticker=ticker, exchange=exchange, timestamp=timestamp, index=index)
    elif index in VOLUME_INDEXES:
        storage = VolumeStorage(ticker=ticker, exchange=exchange, timestamp=timestamp, index=index)
    else:
        logger.error("I don't know what kind of index this is")
        return False

    # logger.debug(f'process price for ticker: {ticker} and index: {index}')

    # eg. key_format = f'{ticker}:{exchange}:PriceVolumeHistoryStorage:{index}'

    query_results = PriceVolumeHistoryStorage.query(
        ticker=ticker, exchange=exchange,
        index=index, timestamp=timestamp,
        periods_range=1, timestamp_tolerance=29
    )

    if not query_results['values_count']:
        return False

    # logger.debug("results from PriceVolumeHistoryStorage query... ")
    # logger.debug(query_results)

    try:
        index_values = [int(v) for v in query_results['values']]
        # timestamps = [int(v) for v in query_results['timestamps']]

        if not len(index_values):
            storage.value = None
        elif index == "close_volume":
            storage.value = index_values[-1]
        elif index == "open_price":
            storage.value = index_values[0]
        elif index == "low_price":
            storage.value = min(index_values)
        elif index == "high_price":
            storage.value = max(index_values)
        elif index == "close_price":
            storage.value = index_values[-1]
        else:
            raise IndexError("unknown index)")

    except IndexError:
        return False # couldn't find a useful value or index (sorry for using this for both definitions of "index")
    except ValueError:
        return False # couldn't find a useful value
    except Exception as e:
        logger.error("there's a bug here: " + str(e))
        return False

    if storage.value:
        storage.save(publish=bool(index == "close_price"))
        # logger.info("saved new thing: " + storage.get_db_key())

    if index == 'close_volume':
        # todo:  for index in derived_volume_indexes: calculate and save volume index
        pass

    if index == "close_price":
        all_values_set = set(index_values)  # these are the close prices
        for other_index in ["open_price", "low_price", "high_price"]:
            query_results = PriceVolumeHistoryStorage.query(
                ticker=ticker, exchange=exchange,
                index=other_index, timestamp=timestamp,
                periods_range=1, timestamp_tolerance=29
            )

            index_values = [int(v) for v in query_results['values']]
            all_values_set = all_values_set | set(index_values)

        if not len(all_values_set):
            logger.error("This shouldn't be possible. Serious bug if you see this!")
            return False

        for d_index in derived_price_indexes:
            price_storage = PriceStorage(ticker=ticker, exchange=exchange, timestamp=timestamp, index=d_index)

            values_set = all_values_set.copy()

            if d_index == "midpoint_price":
                while len(values_set) > 2:
                    values_set.remove(max(values_set))
                    values_set.remove(min(values_set))
                price_storage.value = values_set.pop()

            elif d_index == "mean_price":
                price_storage.value = sum(values_set) / (len(values_set) or 1)

            elif d_index == "price_variance":
                # this is too small of a period size to measure variance
                pass

            if price_storage.value:
                price_storage.save()

    return True
