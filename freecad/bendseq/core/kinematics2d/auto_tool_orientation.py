"""Session-local punch orientation variants; never write library profiles."""
from dataclasses import replace


def variant_key(candidate):
    return (candidate.punch_id, candidate.die_id,
            getattr(candidate, "punch_reversed", None))


def orientation_variants(candidates, punches, *, enabled=False, trace=lambda *a, **k: None):
    """Keep one library-default candidate unless the operator permits testing both.

    An optional sidecar metadata allow_reverse_punch=false prohibits changes.
    Unknown mounting remains unverified; the checkbox is operator permission
    to explore alternatives, not proof of mounting compatibility.
    """
    descriptors = {p.tool_id: p for p in punches}
    variants = []
    for c in candidates:
        descriptor = descriptors.get(c.punch_id)
        allowed = descriptor is not None and descriptor.metadata.get("allow_reverse_punch") is not False
        modes = (False, True) if enabled and allowed else (None,)
        trace("ORIENTATION_VARIANTS", punch=c.punch_id, die=c.die_id,
              modes=modes, reason="OPERATOR_ENABLED" if len(modes) == 2 else
              "LIBRARY_DEFAULT_ONLY" if not enabled else "REVERSAL_PROHIBITED_OR_UNKNOWN_TOOL")
        variants.extend(replace(c, punch_reversed=mode) for mode in modes)
    return tuple(variants)


def oriented_punch(profile, reversed_flag):
    if reversed_flag is None:
        return replace(profile)
    if type(reversed_flag) is not bool:
        raise ValueError("Invalid punch orientation")
    return replace(profile, punch_reversed=reversed_flag)


def restore_matching_punch(profile, reference, matches):
    """Restore the exact saved fingerprint, even when library default differs."""
    if matches(profile, reference):
        return profile
    alternative = oriented_punch(profile, not profile.punch_reversed)
    if matches(alternative, reference):
        return alternative
    raise ValueError("Tool library profile changed: " + str(profile.tool_id))
