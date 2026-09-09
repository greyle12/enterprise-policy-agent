import unittest

from scripts.gate_v3_intent import (
    CITY_RULE_ID,
    FINANCE_RULE_ID,
    ConditionStatus,
    GateDecision,
    RequestAct,
    TOPIC_FINANCE_REVIEW,
    TOPIC_LODGING_AMOUNT,
    decide_intent,
    evaluate,
    parse_query,
    relation_for,
)


class GateV3IntentTests(unittest.TestCase):
    def assert_decision(self, query, rule_id, expected):
        intent, result = evaluate(query, rule_id)
        self.assertEqual(result.decision, expected)
        self.assertTrue(result.reason_code)
        self.assertEqual(intent.query, query)
        return intent, result

    def test_known_city_condition_denies_city_supplement_across_clauses(self):
        intent, result = self.assert_decision(
            "目的地的类别已经核实，是二类。出差人是普通员工。请只给每晚住宿金额上限。",
            CITY_RULE_ID,
            GateDecision.DENY,
        )
        self.assertTrue(
            any(
                condition.status is ConditionStatus.GIVEN and condition.value == "二类城市"
                for condition in intent.conditions
            )
        )
        self.assertTrue(any(request.topic == TOPIC_LODGING_AMOUNT for request in intent.requests))

    def test_unknown_city_condition_allows_lodging_query(self):
        intent, result = self.assert_decision(
            "此次目的地是厦门，但类别还没查。出差人是部门负责人。每晚住宿费用最高多少？",
            CITY_RULE_ID,
            GateDecision.ALLOW,
        )
        self.assertTrue(
            any(condition.status is ConditionStatus.UNKNOWN for condition in intent.conditions)
        )
        self.assertTrue(result.evidence_spans)

    def test_direct_city_list_is_not_applicable(self):
        self.assert_decision(
            "一类城市包括哪些地方？请只列出制度里的城市名单。",
            CITY_RULE_ID,
            GateDecision.NOT_APPLICABLE,
        )

    def test_local_exclusion_does_not_cancel_other_city_need(self):
        intent, _ = self.assert_decision(
            "我不问同住安排，只想知道普通员工去南京住酒店每晚限额。",
            CITY_RULE_ID,
            GateDecision.ALLOW,
        )
        self.assertTrue(
            any(
                request.topic == "room_sharing" and request.act is RequestAct.EXCLUDE_TOPIC
                for request in intent.requests
            )
        )

    def test_only_room_sharing_denies_city_relation(self):
        self.assert_decision(
            "这里说的是两名同性普通员工合住标准间。这种安排能由公司强制执行吗？只回答是否强制。",
            CITY_RULE_ID,
            GateDecision.DENY,
        )

    def test_finance_topic_exclusion_and_exemption_are_distinct(self):
        self.assert_decision(
            "不要展开审批流程，只告诉我普通费用拖到多少天后可能被拒收。",
            FINANCE_RULE_ID,
            GateDecision.DENY,
        )
        intent, _ = self.assert_decision(
            "普通费用交晚了五十天，部门负责人已经批过。这是否意味着不用再交财务复核？",
            FINANCE_RULE_ID,
            GateDecision.ALLOW,
        )
        self.assertTrue(
            any(
                request.topic == TOPIC_FINANCE_REVIEW and request.act is RequestAct.ASK_EXEMPTION
                for request in intent.requests
            )
        )

    def test_double_negative_is_not_exclusion(self):
        intent, _ = self.assert_decision(
            "不是不需要审批流程：这笔普通费用拖了四十天，请说清部门和财务各要做什么。",
            FINANCE_RULE_ID,
            GateDecision.ALLOW,
        )
        self.assertFalse(
            any(
                request.topic == TOPIC_FINANCE_REVIEW and request.act is RequestAct.EXCLUDE_TOPIC
                for request in intent.requests
            )
        )

    def test_scope_only_reason_denies_finance_supplement(self):
        self.assert_decision(
            "普通费用，四十二天后交。只问原因说明：要写吗？",
            FINANCE_RULE_ID,
            GateDecision.DENY,
        )
        self.assert_decision(
            "普通费用晚交了四十天，我只想知道要不要写明迟交原因，其他环节先不讲。",
            FINANCE_RULE_ID,
            GateDecision.DENY,
        )

    def test_negated_exemption_and_composite_need_allow(self):
        self.assert_decision(
            "不是要免掉财务复核，也不想略过部门审批。普通费用迟交六十天，完整要求是什么？",
            FINANCE_RULE_ID,
            GateDecision.ALLOW,
        )
        self.assert_decision(
            "普通员工到青岛出差，每晚能报多少住宿费？两名同性同事还必须合住吗？",
            CITY_RULE_ID,
            GateDecision.ALLOW,
        )

    def test_unresolved_and_conflicting_conditions_fail_closed(self):
        intent, result = self.assert_decision(
            "这个怎么处理？",
            FINANCE_RULE_ID,
            GateDecision.UNRESOLVED,
        )
        self.assertTrue(intent.ambiguities)
        self.assert_decision(
            "目的地已确认是二类城市，但类别还没查。普通员工每晚住宿上限是多少？",
            CITY_RULE_ID,
            GateDecision.UNRESOLVED,
        )

    def test_spans_are_source_preserving_and_direct_finance_query_is_not_applicable(self):
        query = "普通费用，哪些费用必须交财务复核？"
        intent = parse_query(query)
        self.assertTrue(all(query[c.start : c.end] == c.text for c in intent.clauses))
        self.assertTrue(
            all(query[r.span.start : r.span.end] == r.span.text for r in intent.requests)
        )
        self.assertTrue(all(request.referent for request in intent.requests))
        self.assertEqual(
            decide_intent(intent, relation_for(FINANCE_RULE_ID)).decision,
            GateDecision.NOT_APPLICABLE,
        )

    def test_unknown_rule_and_invalid_query_rejected(self):
        with self.assertRaises(ValueError):
            relation_for("made_up_rule")
        with self.assertRaises(ValueError):
            parse_query("   ")
        intent = parse_query("普通费用超过四十天后交，需要哪些审批？")
        with self.assertRaises(ValueError):
            decide_intent(
                intent,
                relation_for(FINANCE_RULE_ID).__class__(
                    rule_id=FINANCE_RULE_ID,
                    source_topic="wrong",
                    target_topic="not_a_frozen_topic",
                ),
            )


if __name__ == "__main__":
    unittest.main()
