from pricing import invoice_total, line_total


def test_no_discount():
    assert line_total(1000, 3) == 3000


def test_discount_on_round_price():
    assert line_total(1000, 2, 10) == 1800


def test_invoice_sums_lines():
    assert invoice_total([(1000, 1, 0), (500, 2, 0)]) == 2000


def test_rejects_negative():
    try:
        line_total(-1, 1)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
