
def round_sig_figs(value, sig_figs: int) -> float:
    value = float(value)
    non_decimal_place = len(str(int(value)))
    # decimal_places = max(len(str(value % 1))-2, 0)

    if value < 0:
        distance_from_decimal = 0
        for char in str(value):
            if char is ".": continue
            elif int(char) > 0: break
            else: distance_from_decimal += 1
        return round(value, (distance_from_decimal+sig_figs))
    else:
        return round(value, (sig_figs-non_decimal_place))
