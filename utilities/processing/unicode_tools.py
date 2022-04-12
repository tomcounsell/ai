import typing
from typing import List, Callable
import unicodedata
import re
import toolz
from toolz import complement  # "flips" result of function, i.e. True == False


# Removes all control characters - Including line feeds and carriage returns.
def clean_text(text: str) -> str:
    return filter_text(text, complement(is_control_char))


def filter_text(text: str, filters: List[Callable[[str], bool]]) -> str:
    # Applies the filters in the order given
    return "".join(list(filter(lambda c: toolz.pipe(c, filters), text)))
    # return "".join(list(filter(lambda x: ord(x) == 32 or 32 < ord(x) < 127,
    #                            text)))


def is_control_char(ch: str) -> bool:
    return unicodedata.category(ch)[0] == "C"


def remove_control_chars(string):
    return "".join(list(filter(complement(is_control_char), string)))


def remove_html_tags(s: str) -> str:
    return re.sub("(?i)<(?!img|/img).*?>", "", s).strip()
