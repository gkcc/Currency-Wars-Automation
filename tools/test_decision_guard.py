"""Offline regression tests: no game windows, network, OCR or input control."""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from decision_guard import inspect


def base():
    return {"snapshot_id": "offline-only", "checks": ["route", "economy", "gear"],
            "route": {"name": "low-cost main", "core_goal": "small Herta 3-star",
                      "current_level": 5, "planned_level": 5, "roll_level": 5,
                      "core_goal_complete": False, "core_owned_copies": 4,
                      "core_target_stars": 3, "claim_complete": False},
            "economy": {"gold": 11, "refresh_cost": 2, "planned_paid_refreshes": 3,
                        "planned_buy_cost": 0, "planned_exp_cost": 0,
                        "reserve_for_target": 4, "final_battle": False},
            "gear": {"main_carry": "The Herta", "position": "front",
                     "damage_reduction_threshold_pct": 20,
                     "team_effect_changed": False,
                     "defensive_items_on_carry": [],
                     "available_offense_items": [],
                     "stacking": "unknown",
                     "team_dr_excluding_carry_items": []}}


class GuardTests(unittest.TestCase):
    def test_small_budget_is_consistent(self):
        result = inspect(base())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["calculations"]["economy"]["remaining"], 5)
        self.assertEqual(result["calculations"]["route_goal"], "small Herta 3-star")
        self.assertEqual(result["calculations"]["core_copies_missing"], 5)

    def test_cannot_refresh_past_reserved_purchase(self):
        s = base()
        s["economy"]["planned_paid_refreshes"] = 4
        self.assertEqual(inspect(s)["status"], "BLOCK")
        self.assertIn("PURCHASE_RESERVE_BREACH", [x["code"] for x in inspect(s)["issues"]])

    def test_route_drift_must_not_be_silent(self):
        s = base()
        s["route"]["planned_level"] = 6
        result = inspect(s)
        self.assertEqual(result["status"], "BLOCK")
        self.assertIn("ROUTE_CHANGE_UNDECLARED", [x["code"] for x in result["issues"]])

    def test_route_switch_is_not_certified_optimal(self):
        s = base()
        s["route"]["planned_level"] = 6
        s["route"]["switch"] = {"to": "high-population group attack", "reason": "immediate 4-scholar gain",
                                 "new_core_goal": "The Herta 2-star", "new_roll_level": 7,
                                 "buy_priority": ["The Herta", "Naxia"]}
        result = inspect(s)
        self.assertEqual(result["status"], "REVIEW")
        self.assertIn("ROUTE_SWITCH_REQUIRES_COMPARISON", [x["code"] for x in result["issues"]])

    def test_unjustified_final_unused_gold(self):
        s = base()
        s["economy"].update({"gold": 31, "planned_paid_refreshes": 0,
                             "reserve_for_target": 0, "final_battle": True})
        result = inspect(s)
        self.assertEqual(result["status"], "REVIEW")
        self.assertIn("FINAL_GOLD_UNUSED", [x["code"] for x in result["issues"]])
        s["economy"]["unspent_reason"] = "no feasible immediate upgrade after shop and bench review"
        self.assertEqual(inspect(s)["status"], "PASS")

    def test_main_carry_gear_after_team_buffs(self):
        s = base()
        s["gear"].update({
            "main_carry": "The Herta", "team_effect_changed": True,
            "defensive_items_on_carry": ["Adaptive exoskeleton"],
            "available_offense_items": ["Attack item A"],
            "stacking": "multiplicative",
            "team_dr_excluding_carry_items": [
                {"name": "Catcake", "pct": 15, "scope": "front", "verified_active": True},
                {"name": "Heavy Armor", "pct": 18, "scope": "all", "verified_active": True}]})
        result = inspect(s)
        self.assertEqual(result["status"], "REVIEW")
        self.assertAlmostEqual(result["calculations"]["gear"]["team_dr_without_carry_items_pct"], 30.3)
        self.assertIn("CARRY_GEAR_MARGINAL_VALUE", [x["code"] for x in result["issues"]])

    def test_unknown_stacking_does_not_assert_threshold(self):
        s = base()
        s["gear"].update({"stacking": "unknown", "team_effect_changed": True,
                          "defensive_items_on_carry": ["Adaptive exoskeleton"],
                          "available_offense_items": ["Attack item A"],
                          "team_dr_excluding_carry_items": [
                              {"name": "Catcake", "pct": 15, "scope": "front", "verified_active": True},
                              {"name": "Heavy Armor", "pct": 18, "scope": "all", "verified_active": True}]})
        result = inspect(s)
        self.assertNotIn("team_dr_without_carry_items_pct", result["calculations"]["gear"])
        self.assertIn("STACKING_UNVERIFIED", [x["code"] for x in result["issues"]])

    def test_maximum_stacking_does_not_claim_threshold(self):
        s = base()
        s["gear"].update({"stacking": "maximum", "team_effect_changed": True,
                          "defensive_items_on_carry": ["Adaptive exoskeleton"],
                          "available_offense_items": ["Attack item A"],
                          "team_dr_excluding_carry_items": [
                              {"name": "Catcake", "pct": 15, "scope": "front", "verified_active": True},
                              {"name": "Heavy Armor", "pct": 18, "scope": "all", "verified_active": True}]})
        result = inspect(s)
        self.assertEqual(result["calculations"]["gear"]["team_dr_without_carry_items_pct"], 18)
        self.assertNotIn("CARRY_GEAR_MARGINAL_VALUE", [x["code"] for x in result["issues"]])

    def test_missing_gold_blocks_not_assumes(self):
        s = base()
        del s["economy"]["gold"]
        result = inspect(s)
        self.assertEqual(result["status"], "BLOCK")
        self.assertIn("INVALID_ECONOMY_INPUT", [x["code"] for x in result["issues"]])

    def test_false_three_star_claim(self):
        s = base()
        s["route"]["claim_complete"] = True
        result = inspect(s)
        self.assertEqual(result["status"], "BLOCK")
        self.assertIn("UNREACHABLE_STAR_CLAIM", [x["code"] for x in result["issues"]])

    def test_invalid_false_as_zero(self):
        s = base()
        s["economy"]["gold"] = False
        self.assertEqual(inspect(s)["status"], "BLOCK")

    def test_cli_exit_and_json(self):
        script = os.path.join(HERE, "decision_guard.py")
        p = subprocess.run([sys.executable, script], input=json.dumps(base()),
                           encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "PASS")
        s = base()
        s["route"]["planned_level"] = 6
        p = subprocess.run([sys.executable, script], input=json.dumps(s),
                           encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stdout)["status"], "BLOCK")


if __name__ == "__main__":
    unittest.main()
