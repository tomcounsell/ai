import logging
from apps.finance.TA import TAException
from apps.finance.TA.storages.abstract.key_value import KeyValueStorage

logger = logging.getLogger(__name__)


class StorageException(TAException):
    pass

class VolumeseriesException(TAException):
    pass


class VolumeseriesStorage(KeyValueStorage):
    """
    stores things in a sorted set unique to each ticker and exchange
    ordered by blocks of volume as opposed to time
    """
    class_describer = "volumeseries"
