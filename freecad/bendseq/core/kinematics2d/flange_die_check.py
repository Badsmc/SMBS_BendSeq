"""Nominal full-V flange assessment shared by table, banner and selection."""
from math import isfinite
from .gauge_motion_limits import flange_below_opening


def positive_mm(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value.replace(',', '.') if isinstance(value, str) else value)
    except (ValueError, TypeError):
        return None
    return result if isfinite(result) and result > 0 else None


def die_opening_mm(die):
    if die is None:
        return None
    datums = getattr(die, 'die_datums', None)
    for value in (getattr(die, 'v_opening_mm', None), getattr(datums, 'v_opening_mm', None)):
        result = positive_mm(value)
        if result is not None:
            return result
    # Authoritative shoulder datums, never the die body width or its name.
    if datums is not None:
        try:
            return positive_mm(abs(datums.right_shoulder[0] - datums.left_shoulder[0]))
        except (AttributeError, TypeError, IndexError):
            pass
    return None


def assess_flanges(session, die):
    opening = die_opening_mm(die)
    dimensions = getattr(session, 'local_bend_dimensions_mm', {}) if session is not None else {}
    order = tuple(getattr(session, 'canonical_order', ()))
    known = {bid: positive_mm(dimensions.get(bid)) for bid in order}
    return dict(opening_mm=opening, dimensions_mm=known,
                short_bends=tuple((bid, value) for bid, value in known.items()
                                  if flange_below_opening(value, opening)),
                unknown_bends=tuple(bid for bid, value in known.items() if value is None))
