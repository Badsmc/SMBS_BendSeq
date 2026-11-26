# Backgauge Z travel and minimum-flange warning

Branch: fix/backgauge-z-travel-flange-warning, from develop b37d0b6.

The visual CAD backgauge uses the existing persisted `z_min_mm` / `z_max_mm`
limits (default -20..+20 mm). Both limits can now be edited in the backgauge
settings. Z is the centre of the actual CAD working face in machine coordinates.
Old configs and FCStd sessions keep the same fields and values.

Automatic setup retains the previous height while the actual working face can
still cover the requested sheet-edge elevation. Otherwise it moves only as far
as needed within the configured travel. Manual targets are also bounded.
If the gauge CAD body's horizontal envelope overlaps the mounted die in both
S and X, the bottom of the entire body must remain at or above the die top.
Away from the die it may descend down to the configured minimum. Unknown die
length is treated conservatively as unbounded along X. Explicit segmented die
spans preserve gaps.

Transfer is split into raise / traverse / lower. Its swept S/X envelope is
checked before choosing a transit height, preventing a low diagonal path
through the die. If no height within travel can clear the die, the gauge is
hidden and an explicit warning is shown. If the working face cannot reach the
sheet after limiting Z, a different warning is shown. Missing gauge CAD bounds
also hide it with an explicit diagnostic; no unrestricted fallback is rendered.
CAD envelope protection is conservative, not an exact OCC collision check.
Search algorithms and their collision verdicts remain unchanged. Setup choices
are logged as BACKGAUGE_SETUP_LIMITS.

The operations table and scene status show a warning when the existing nominal
local flange dimension is less than the selected die's full V opening. This is
the operator-requested full-V rule, not a general calculation of minimum flange
for every angle/material/process. No warning is fabricated when either value is
unknown. Switching tooling or a bend updates the check. This is a warning only;
it does not rewrite FAST scores or physically certify other configurations.

Validation: headless unit/regression tests, syntax, preflight and TS/QM generation.
Actual CAD movement and warnings must still be inspected in FreeCAD 1.1.3+:
- Two consecutive edges within the working-face height: Z stays unchanged.
- Edge outside that height: only necessary bounded Z movement.
- Below-die target with body overlapping die: whole body stays above die top.
- Same target beyond die depth or length: descent allowed within travel.
- Transfer over die: rise first, traverse high, descend outside die.
- Insufficient upper travel: hidden gauge and explicit warning.
- 10 mm flange with V12: warning in table and scene; 12/13 mm: no full-V warning.
- Save/reopen backgauge settings with changed Z range.

The completed migration AST audit now reads the fixed migration commit instead
of applying the historical no-algorithm-change requirement to all future
features. It still verifies all 82 original modules and the original session
serializer against the pre-migration baseline. Current code is covered by tests
and preflight. No new dependency was added.

## Follow-up: warning visibility on die selection
The Windows journal from 2026-11-26 confirmed the prior branch bytes but exposed
that die selection did not refresh operation rows, and the scene warning only
covered the active bend. It could therefore miss the operator's attention when
a different bend had a short flange.

Die selection now refreshes the table, assesses every canonical bend, displays
one popup per changed unsuitable selection and maintains a bold multiline
warning above the viewer. Routine playback refreshes the banner without modal
spam. Unspecified V or flange measurements are explicitly shown as unverified,
not silently passed. V can come from the numeric catalog field or authoritative
die shoulder datums; die body width and names are never used as V. The diagnostic
FLANGE_DIE_CHECK records opening, dimensions, short bends and unknown bends.

Regression tests cover V8 -> V32 with the active 50 mm flange and an inactive
10 mm flange, actual banner/popup execution without Qt, non-repetition, switching
to a suitable die, and unknown metadata. GUI placement still needs host review.
