import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.checkers.subscriptions import check_success


SUBSCRIPTION_TASKS = [
    "cancel_unused_subscriptions",
    "free_trial_conversion",
    "price_increase_review",
    "downgrade_by_usage",
]


def load_task(task_id: str) -> dict:
    return json.loads((ROOT / f"benchmark/tasks/{task_id}.json").read_text())


def gold_result(task: dict) -> dict:
    """Hand-built final state of a correct run, acting on the start date."""
    initial_plans = {sub["id"]: sub["plan"] for sub in task["subscriptions"]}
    subscriptions = {}
    for sub_id, want in task["expected"]["subscriptions"].items():
        cancelled = want["status"] == "cancelled"
        subscriptions[sub_id] = {
            "status": want["status"],
            "plan": want.get("plan", initial_plans[sub_id]),
            "cancelled_on": task["start_date"] if cancelled else None,
        }
    charges = deepcopy(task["expected"]["charges"])
    return {
        "date": task["horizon"],
        "balance_cents": task["initial_balance_cents"] - sum(charge["amount_cents"] for charge in charges),
        "subscriptions": subscriptions,
        "charges": charges,
    }


def keep(result: dict, sub_id: str, charge: dict) -> None:
    """Simulate failing to cancel a subscription that then renews."""
    result["subscriptions"][sub_id].update(status="active", cancelled_on=None)
    result["charges"].append(charge)


def cancel(result: dict, sub_id: str, charge_date: str) -> None:
    """Simulate wrongly cancelling a subscription that should stay active."""
    result["subscriptions"][sub_id].update(status="cancelled", cancelled_on="2026-10-01")
    result["charges"] = [c for c in result["charges"] if not (c["subscription_id"] == sub_id and c["date"] == charge_date)]


class FixtureConsistencyTests(unittest.TestCase):
    def test_expected_block_covers_every_subscription(self):
        for task_id in SUBSCRIPTION_TASKS:
            task = load_task(task_id)
            ids = {sub["id"] for sub in task["subscriptions"]}
            self.assertEqual(set(task["expected"]["subscriptions"]), ids, task_id)
            self.assertEqual(task["id"], task_id)

    def test_document_ids_exist(self):
        for task_id in SUBSCRIPTION_TASKS:
            task = load_task(task_id)
            doc_ids = {doc["id"] for doc in task["documents"]}
            for sub in task["subscriptions"]:
                self.assertIn(sub["document_id"], doc_ids, task_id)
                self.assertIn(sub["plan"], sub["plans"], task_id)

    def test_gold_result_passes(self):
        for task_id in SUBSCRIPTION_TASKS:
            task = load_task(task_id)
            self.assertTrue(check_success(task, gold_result(task)), task_id)

    def test_unfinished_run_fails(self):
        for task_id in SUBSCRIPTION_TASKS:
            task = load_task(task_id)
            result = gold_result(task)
            result["date"] = task["start_date"]
            self.assertFalse(check_success(task, result), task_id)

    def test_overdraft_fails(self):
        task = load_task("cancel_unused_subscriptions")
        result = gold_result(task)
        result["balance_cents"] = -1
        self.assertFalse(check_success(task, result))


class CancelUnusedSubscriptionsTests(unittest.TestCase):
    def setUp(self):
        self.task = load_task("cancel_unused_subscriptions")
        self.result = gold_result(self.task)

    def test_keeping_unused_music_fails(self):
        keep(self.result, "tunebox", {"subscription_id": "tunebox", "date": "2026-10-08", "amount_cents": 1099})
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_backup_only_service_fails(self):
        cancel(self.result, "photovault", "2026-10-03")
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_recently_used_news_fails(self):
        cancel(self.result, "dailyledger", "2026-10-15")
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_after_renewal_fails(self):
        self.result["subscriptions"]["fitpath"]["cancelled_on"] = "2026-10-21"
        self.result["charges"].append({"subscription_id": "fitpath", "date": "2026-10-20", "amount_cents": 1999})
        self.assertFalse(check_success(self.task, self.result))


class FreeTrialConversionTests(unittest.TestCase):
    def setUp(self):
        self.task = load_task("free_trial_conversion")
        self.result = gold_result(self.task)

    def test_cancelling_inside_notice_period_fails(self):
        self.result["subscriptions"]["snapedit"]["cancelled_on"] = "2026-10-05"
        self.result["charges"].append({"subscription_id": "snapedit", "date": "2026-10-06", "amount_cents": 999})
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_late_even_without_charge_fails(self):
        self.result["subscriptions"]["snapedit"]["cancelled_on"] = "2026-10-05"
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_old_fitpath_trial_fails(self):
        cancel(self.result, "fitpath", "2026-10-20")
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_wanted_trial_fails(self):
        cancel(self.result, "lingualoop", "2026-10-09")
        self.assertFalse(check_success(self.task, self.result))


class PriceIncreaseReviewTests(unittest.TestCase):
    def setUp(self):
        self.task = load_task("price_increase_review")
        self.result = gold_result(self.task)

    def test_paying_increased_streamflix_fails(self):
        keep(self.result, "streamflix", {"subscription_id": "streamflix", "date": "2026-10-22", "amount_cents": 1799})
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_annual_plan_by_yearly_price_fails(self):
        cancel(self.result, "photovault", "2026-10-25")
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_exactly_fifteen_dollars_fails(self):
        cancel(self.result, "dailyledger", "2026-10-15")
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_price_decrease_fails(self):
        cancel(self.result, "gamepass", "2026-10-16")
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_every_price_increase_fails(self):
        cancel(self.result, "tunebox", "2026-10-18")
        self.assertFalse(check_success(self.task, self.result))


class DowngradeByUsageTests(unittest.TestCase):
    def setUp(self):
        self.task = load_task("downgrade_by_usage")
        self.result = gold_result(self.task)

    def test_plan_from_latest_month_only_fails(self):
        self.result["subscriptions"]["northwind"]["plan"] = "5gb"
        self.result["charges"][1]["amount_cents"] = 2500
        self.assertFalse(check_success(self.task, self.result))

    def test_storage_plan_too_small_fails(self):
        self.result["subscriptions"]["photovault"]["plan"] = "200gb"
        self.result["charges"][0]["amount_cents"] = 299
        self.assertFalse(check_success(self.task, self.result))

    def test_unchanged_plan_fails(self):
        self.result["subscriptions"]["streamflix"]["plan"] = "premium"
        self.result["charges"][2]["amount_cents"] = 2299
        self.assertFalse(check_success(self.task, self.result))

    def test_change_after_renewal_fails(self):
        self.result["charges"][1]["amount_cents"] = 6000
        self.assertFalse(check_success(self.task, self.result))

    def test_cancelling_fails(self):
        cancel(self.result, "streamflix", "2026-10-12")
        self.assertFalse(check_success(self.task, self.result))


if __name__ == "__main__":
    unittest.main()
