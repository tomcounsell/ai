"""Line totals for an invoice. Quantities are whole units; prices are cents."""


def line_total(unit_cents: int, quantity: int, discount_pct: int = 0) -> int:
    """Total for one line after a percentage discount, in cents, rounded down."""
    if quantity < 0 or unit_cents < 0:
        raise ValueError("negative quantity or price")
    if not 0 <= discount_pct <= 100:
        raise ValueError("discount out of range")
    gross = unit_cents * quantity
    # bug: discount applied to the unit price and truncated per unit, so a
    # 5% discount on a 99-cent item rounds to zero per unit and the invoice
    # charges full price on every line under a dollar
    discounted_unit = unit_cents * (100 - discount_pct) // 100
    return discounted_unit * quantity


def invoice_total(lines: list[tuple[int, int, int]]) -> int:
    return sum(line_total(u, q, d) for u, q, d in lines)
