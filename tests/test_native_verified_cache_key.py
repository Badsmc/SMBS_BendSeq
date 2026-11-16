"""Regression: native D6 step reuse should survive elapsed-time budget changes.

Only *previously converged* CLEAR steps are cacheable by the caller.
Changing physical/sampling semantics must still invalidate the cache.
"""
import unittest
from types import SimpleNamespace
from smbs_core.kinematics2d.manual_physical_validation import (
    _native_step_cache_config_signature,
)


class NativeVerifiedCacheKeyTests(unittest.TestCase):
    def test_phase_clock_limit_does_not_invalidate_converged_evidence(self):
        cfg1 = SimpleNamespace(
            max_phase_time_s=40.0,
            max_recursion_depth=7,
            max_evaluated_states_per_phase=72,
            volume_tolerance_mm3=1e-4,
            near_tolerance_mm=0.5,
        )
        cfg2 = SimpleNamespace(**{**vars(cfg1), "max_phase_time_s": 60.0})
        self.assertEqual(
            _native_step_cache_config_signature(cfg1),
            _native_step_cache_config_signature(cfg2),
        )

    def test_physical_and_sampling_controls_still_invalidate(self):
        base = SimpleNamespace(
            max_phase_time_s=40.0,
            max_recursion_depth=7,
            max_evaluated_states_per_phase=72,
            volume_tolerance_mm3=1e-4,
            near_tolerance_mm=0.5,
        )
        baseline = _native_step_cache_config_signature(base)
        for name, value in (
            ("max_recursion_depth", 6),
            ("max_evaluated_states_per_phase", 48),
            ("volume_tolerance_mm3", 0.01),
            ("near_tolerance_mm", 1.0),
        ):
            variant = SimpleNamespace(**{**vars(base), name: value})
            with self.subTest(name=name):
                self.assertNotEqual(
                    baseline,
                    _native_step_cache_config_signature(variant),
                )


if __name__ == "__main__":
    unittest.main()
