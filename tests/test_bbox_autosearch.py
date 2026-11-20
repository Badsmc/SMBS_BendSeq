import unittest
from smbs_core.kinematics2d.bbox_autosearch import SearchOperation as Op, search


class SearchTests(unittest.TestCase):
    def test_backtracks_and_explores_independent_orientation(self):
        seen = []
        def oracle(path, poses, complete):
            seen.append((tuple(o.operation_id for o in path), dict(poses)))
            blocked = path[0].bend_id == 'A' or not poses[path[-1].operation_id]
            return {'status': 'COLLISION' if blocked else 'UNVERIFIED', 'cost': 1}
        results = [e for e in search([Op('A','A',90), Op('B','B',90)], oracle)
                   if e['kind'] == 'result']
        self.assertTrue(results)
        self.assertEqual([o.operation_id for o in results[0]['operations']], ['B','A'])
        self.assertTrue(results[0]['poses']['A'])

    def test_stages_keep_precedence_and_unknown_evidence(self):
        results = [e for e in search([Op('pre','B',30), Op('final','B',90,'pre')],
                   lambda *a: {'status':'UNVERIFIED_STAGES'}, max_results=4)
                   if e['kind'] == 'result']
        self.assertEqual(len(results), 4)
        self.assertTrue(all([o.operation_id for o in r['operations']] == ['pre','final']
                            for r in results))
        self.assertTrue(all(r['report']['status'] == 'UNVERIFIED_STAGES' for r in results))

    def test_node_budget_and_cancellation(self):
        events = list(search([Op('A','A',90), Op('B','B',90)],
                      lambda *a: {'status':'UNVERIFIED'}, max_nodes=1))
        self.assertEqual(events[-1]['reason'], 'LIMIT')
        self.assertEqual(events[-1]['nodes'], 1)
        self.assertEqual(list(search([Op('A','A',90)], lambda *a: {},
                                    cancelled=lambda: True))[-1]['reason'], 'CANCELLED')

    def test_collision_never_becomes_result(self):
        events = list(search([Op('A','A',90)], lambda *a: {'status':'COLLISION'}))
        self.assertFalse(any(e['kind'] == 'result' for e in events))
        self.assertEqual(events[-1]['reason'], 'EXHAUSTED')

    def test_final_check_runs_for_each_complete_pose(self):
        flags = []
        def oracle(path, poses, complete):
            flags.append(complete)
            return {'status':'UNVERIFIED'}
        list(search([Op('A','A',90),Op('B','B',90)], oracle, max_results=2))
        self.assertIn(False, flags)
        self.assertEqual(flags.count(True), 2)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            list(search([Op('A','A',90),Op('A','A',40)], lambda *a: {}))
