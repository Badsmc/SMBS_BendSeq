import unittest
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.geometry_backend.base import InvalidRotationError, CollisionCheckResult
from smbs_core.geometry_backend.io.step_reader import StepReader, StepReadError

class TestPhase2Backend(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()

    def test_mock_backend_implements_abc(self):
        shape = self.backend.make_box(10, 20, 30)
        self.assertTrue(self.backend.is_valid(shape))
        self.assertEqual(self.backend.get_bounding_box(shape), (0.0, 0.0, 0.0, 10, 20, 30))

    def test_rotate_empty_faces_raises(self):
        shape = self.backend.make_box(10, 20, 30)
        with self.assertRaises(InvalidRotationError):
            self.backend.rotate_subtree(shape, (0, 0, 0), (1, 0, 0), 45.0, moving_panels=set())

    def test_rotate_subtree_with_moving_panels(self):
        shape = self.backend.make_box(10, 20, 30)
        rotated = self.backend.rotate_subtree(shape, (0, 0, 0), (1, 0, 0), 45.0, moving_panels={"PANEL_1"})
        self.assertEqual(rotated["last_rotation"], 45.0)
        self.assertEqual(rotated["moving_panels"], ["PANEL_1"])

    def test_intersect_volume_returns_structured_result(self):
        shape1 = self.backend.make_box(10, 20, 30)
        shape2 = self.backend.make_box(10, 20, 30)
        res = self.backend.intersect_volume(shape1, shape2)
        self.assertIsInstance(res, CollisionCheckResult)
        self.assertEqual(res.status, "valid")
        self.assertFalse(res.collides)

    def test_boolean_failure_returns_unknown(self):
        res = self.backend.intersect_volume(None, None)
        self.assertIsInstance(res, CollisionCheckResult)
        self.assertEqual(res.status, "unknown")

    def test_face_hash_stable_across_runs(self):
        face = {"id": "FACE_1", "type": "plane"}
        hash1 = self.backend.get_face_hash(face)
        hash2 = self.backend.get_face_hash(face)
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 16)

    def test_is_valid_rejects_broken_shape(self):
        broken_shape = {"is_broken": True}
        self.assertFalse(self.backend.is_valid(broken_shape))
        self.assertFalse(self.backend.is_valid(None))

    def test_step_reader_file_not_found_raises(self):
        reader = StepReader(self.backend)
        with self.assertRaises(StepReadError):
            reader.read_step("non_existent_file.step")

    def test_step_reader_invalid_shape_raises(self):
        class BrokenShapeBackend(MockBackend):
            def load_shape(self, filepath):
                return {"is_broken": True}

        reader = StepReader(BrokenShapeBackend())
        with self.assertRaises(StepReadError):
            reader.read_step("some_file.step")

if __name__ == "__main__":
    unittest.main()
