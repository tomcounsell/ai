import random

from retry import backoff_delays


def test_length():
    assert len(backoff_delays(5)) == 5
    assert backoff_delays(0) == []


def test_each_delay_within_bound():
    random.seed(7)
    for n, d in enumerate(backoff_delays(12, base=0.5, cap=30.0)):
        assert 0 <= d <= min(30.0, 0.5 * 2**n)


def test_cap_binds_late_attempts():
    random.seed(7)
    assert all(d <= 30.0 for d in backoff_delays(20))


def test_rejects_negative():
    try:
        backoff_delays(-1)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
