# https://stackoverflow.com/questions/16974047/efficient-way-to-find-missing-elements-in-an-integer-sequence/16974075#16974075
from itertools import islice, chain

def window(seq, n=2):
    "Returns a sliding window (of width n) over data from the iterable"
    "   s -> (s0,s1,...s[n-1]), (s1,s2,...,sn), ...                   "
    it = iter(seq)
    result = tuple(islice(it, n))
    if len(result) == n:
        yield result
    for elem in it:
        result = result[1:] + (elem,)
        yield result


def missing_elements(L):
    missing = chain.from_iterable(range(x + 1, y) for x, y in window(L) if (y - x) > 1)
    return list(missing)
