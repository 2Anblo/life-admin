"""Checker tests for the schema_version 3 tasks that the simulator cannot run yet.

Each task gets a hand-built final result for a correct run (built from the
task's hidden expectation) and the specific wrong results its traps target.
"""

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.checkers import merchant_cases, move_out, reminders  # noqa: E402
from benchmark.checkers.common import HarnessError  # noqa: E402

CASE_TASKS = [
    "refund_within_return_window",
    "refund_descriptor_reconciliation",
    "partial_refund_with_fee",
    "split_tender_refund",
    "missing_refund_escalation",
    "duplicate_charge_dispute",
]
ALL_V3_TASKS = CASE_TASKS + ["renewal_reminders", "move_out_closeout"]


def load_task(task_id):
    return json.loads((ROOT / f"benchmark/tasks/{task_id}.json").read_text(encoding="utf-8"))


def act(date, tool, args, outcome="accepted", reason=None):
    action = {"date": date, "tool": tool, "args": args, "outcome": outcome}
    if reason:
        action["reason"] = reason
    return action


# ----------------------------------------------------------------------
# Refund and dispute tasks
# ----------------------------------------------------------------------

def gold_case_result(task):
    """Final result of a correct run, built from the hidden expectation."""
    expected = task["hidden"]["expected"]
    actions = []
    for req in expected["refund_requests"]:
        amount = sum(a["amount_cents"] for a in req["allocations"])
        actions.append(act(task["start_date"], "request_refund", {
            "order_id": req["order_id"], "items": list(req["items"]), "amount_cents": amount,
            "allocations": deepcopy(req["allocations"])}))
    for esc in expected["escalations"]:
        actions.append(act(esc["from"], "escalate_case", {"case_id": esc["case_id"], "note": "late"}))
    for dispute in expected["disputes"]:
        actions.append(act(task["start_date"], "open_dispute",
                           {"transaction_id": dispute["transaction_ids"][0], "reason": dispute["reason"]}))
    cases = {c["case_id"]: {"status": "open", "closed_on": None, "matched_transaction_id": None} for c in task["cases"]}
    for closure in expected["closures"]:
        actions.append(act(closure["not_before"], "close_case", {"case_id": closure["case_id"], "resolution": "received"}))
        cases[closure["case_id"]] = {"status": "closed", "closed_on": closure["not_before"],
                                     "matched_transaction_id": closure.get("matched_transaction_id")}
    ledger = [{"transaction_id": f"tx-gold-{n}", "date": credit["by"], "account": credit["account"],
               "amount_cents": credit["amount_cents"], "kind": "refund"}
              for n, credit in enumerate(expected["refund_credits"])]
    return {"date": task["horizon"], "actions": actions, "ledger": ledger, "cases": cases}


class CaseTaskTestCase(unittest.TestCase):
    task_id = None

    def setUp(self):
        self.task = load_task(self.task_id)
        self.result = gold_case_result(self.task)

    def verdict(self):
        return merchant_cases.check(self.task, self.result)

    def assertPasses(self):
        verdict = self.verdict()
        self.assertTrue(verdict["success"], verdict)

    def assertFailsOn(self, *predicates):
        verdict = self.verdict()
        self.assertFalse(verdict["success"], verdict)
        failed = {k for k, p in verdict["predicates"].items() if not p["ok"]}
        self.assertTrue(set(predicates) <= failed, (predicates, failed))

    def actions(self, tool):
        return [a for a in self.result["actions"] if a["tool"] == tool]

    def add_refund(self, account, amount, date):
        self.result["ledger"].append({"transaction_id": f"tx-extra-{len(self.result['ledger'])}", "date": date,
                                      "account": account, "amount_cents": amount, "kind": "refund"})


class FixtureConsistencyTests(unittest.TestCase):
    def test_ids_and_schema(self):
        for task_id in ALL_V3_TASKS:
            task = load_task(task_id)
            self.assertEqual(task["id"], task_id)
            self.assertEqual(task["schema_version"], 3)
            self.assertIn(":", task["hidden"]["checker"])
            self.assertLess(task["start_date"], task["horizon"])

    def test_references_exist(self):
        for task_id in CASE_TASKS:
            task = load_task(task_id)
            accounts = {a["id"] for a in task["accounts"]}
            case_ids = {c["case_id"] for c in task["cases"]}
            tx_ids = {t["transaction_id"] for t in task["transactions"]}
            expected = task["hidden"]["expected"]
            for req in expected["refund_requests"]:
                order = next(o for o in task["orders"] if o["order_id"] == req["order_id"])
                self.assertTrue(set(req["items"]) <= {i["item_id"] for i in order["items"]}, task_id)
                self.assertTrue({a["account"] for a in req["allocations"]} <= accounts, task_id)
            for credit in expected["refund_credits"]:
                self.assertIn(credit["account"], accounts, task_id)
            for item in expected["escalations"] + expected["closures"]:
                self.assertIn(item["case_id"], case_ids, task_id)
            for dispute in expected["disputes"]:
                self.assertTrue(set(dispute["transaction_ids"]) <= tx_ids, task_id)

    def test_gold_results_pass(self):
        for task_id in CASE_TASKS:
            task = load_task(task_id)
            self.assertTrue(merchant_cases.check(task, gold_case_result(task))["success"], task_id)

    def test_result_before_horizon_is_a_harness_error(self):
        task = load_task("refund_within_return_window")
        result = gold_case_result(task)
        result["date"] = task["start_date"]
        with self.assertRaises(HarnessError):
            merchant_cases.check(task, result)


class RefundWithinReturnWindowTests(CaseTaskTestCase):
    task_id = "refund_within_return_window"

    def test_gold_passes(self):
        self.assertPasses()

    def test_refunding_the_whole_receipt_fails(self):
        req = self.actions("request_refund")[0]["args"]
        req.update(items=["L-208", "C-311"], amount_cents=9250, allocations=[{"account": "visa-4421", "amount_cents": 9250}])
        self.add_refund("visa-4421", 1800, "2026-10-17")
        self.assertFailsOn("refund_requests_correct", "refund_money_correct")

    def test_also_requesting_the_unreturned_phone_case_fails(self):
        self.result["actions"].append(act("2026-10-10", "request_refund", {
            "order_id": "BW-55120", "items": ["C-311"], "amount_cents": 1800,
            "allocations": [{"account": "visa-4421", "amount_cents": 1800}]}))
        self.assertFailsOn("refund_requests_correct")

    def test_default_store_credit_fails(self):
        req = self.actions("request_refund")[0]["args"]
        del req["allocations"]
        self.result["ledger"] = []
        self.add_refund("brightway-store-credit", 7450, "2026-10-13")
        self.assertFailsOn("refund_requests_correct", "refund_money_correct")

    def test_requesting_after_the_deadline_fails(self):
        self.actions("request_refund")[0]["date"] = "2026-10-15"
        self.assertFailsOn("refund_requests_correct")

    def test_requesting_twice_fails(self):
        self.result["actions"].append(deepcopy(self.actions("request_refund")[0]))
        self.assertFailsOn("refund_requests_correct")


class RefundDescriptorReconciliationTests(CaseTaskTestCase):
    task_id = "refund_descriptor_reconciliation"

    def test_gold_passes(self):
        self.assertPasses()

    def test_matching_the_rewards_credit_fails(self):
        self.result["cases"]["case-harbor-r204"]["matched_transaction_id"] = "tx-5512"
        self.assertFailsOn("cases_closed_correctly")

    def test_trusting_the_dashboard_and_escalating_harbor_fails(self):
        self.result["actions"] = [a for a in self.result["actions"] if a["tool"] != "close_case"]
        self.result["cases"]["case-harbor-r204"].update(status="open", closed_on=None, matched_transaction_id=None)
        self.result["actions"].append(act("2026-10-16", "escalate_case", {"case_id": "case-harbor-r204"}))
        self.assertFailsOn("cases_closed_correctly", "escalations_correct")

    def test_closing_every_case_fails(self):
        self.result["actions"] = [a for a in self.result["actions"] if a["tool"] != "escalate_case"]
        self.result["actions"].append(act("2026-10-16", "close_case", {"case_id": "case-lumen-r311"}))
        self.result["cases"]["case-lumen-r311"].update(status="closed", closed_on="2026-10-16")
        self.assertFailsOn("cases_closed_correctly", "escalations_correct")

    def test_new_refund_request_instead_of_escalation_fails(self):
        self.result["actions"] = [a for a in self.result["actions"] if a["tool"] != "escalate_case"]
        self.result["actions"].append(act("2026-10-16", "request_refund", {"order_id": "LB-2207", "items": [], "amount_cents": 3500}))
        self.assertFailsOn("refund_requests_correct", "escalations_correct")

    def test_dispute_instead_of_escalation_fails(self):
        self.result["actions"].append(act("2026-10-16", "open_dispute", {"transaction_id": "tx-5541", "reason": "not_received"}))
        self.assertFailsOn("disputes_correct")


class PartialRefundWithFeeTests(CaseTaskTestCase):
    task_id = "partial_refund_with_fee"

    def test_gold_passes(self):
        self.assertPasses()

    def _request(self, items, amount):
        req = self.actions("request_refund")[0]["args"]
        req.update(items=items, amount_cents=amount, allocations=[{"account": "amex-1009", "amount_cents": amount}])

    def test_requesting_the_invoice_price_fails(self):
        self._request(["X-110"], 6000)
        self.assertFailsOn("refund_requests_correct")

    def test_requesting_the_order_total_fails(self):
        self._request(["M-27Q", "X-110"], 26000)
        self.assertFailsOn("refund_requests_correct")

    def test_disputing_the_fee_fails(self):
        self.result["actions"].append(act("2026-10-12", "open_dispute", {"transaction_id": "tx-7120", "reason": "incorrect_amount"}))
        self.assertFailsOn("disputes_correct")

    def test_refund_not_posted_fails(self):
        self.result["ledger"] = []
        self.assertFailsOn("refund_money_correct")


class SplitTenderRefundTests(CaseTaskTestCase):
    task_id = "split_tender_refund"

    def test_gold_passes(self):
        self.assertPasses()

    def test_whole_refund_to_debit_card_fails(self):
        req = self.actions("request_refund")[0]["args"]
        req["allocations"] = [{"account": "debit-5520", "amount_cents": 18000}]
        self.result["ledger"] = []
        self.add_refund("debit-5520", 16200, "2026-10-16")
        self.add_refund("debit-5520", 1800, "2026-10-18")
        self.assertFailsOn("refund_requests_correct", "refund_money_correct")

    def test_closing_after_the_gift_card_credit_fails(self):
        self.actions("close_case")[0]["date"] = "2026-10-14"
        self.assertFailsOn("cases_closed_correctly")

    def test_accepting_the_short_refund_fails(self):
        self.result["actions"] = [a for a in self.result["actions"] if a["tool"] != "escalate_case"]
        self.result["ledger"] = []
        self.add_refund("debit-5520", 10800, "2026-10-16")
        self.add_refund("soundhub-gift-card", 6000, "2026-10-14")
        self.assertFailsOn("escalations_correct", "refund_money_correct")

    def test_escalating_too_early_fails(self):
        self.result["actions"].insert(0, act("2026-10-13", "escalate_case", {"case_id": "case-sh-4471"},
                                             outcome="rejected", reason="premature"))
        self.assertFailsOn("no_premature_escalation")

    def test_requesting_after_the_batch_cutoff_fails(self):
        self.actions("request_refund")[0]["date"] = "2026-10-13"
        self.assertFailsOn("refund_requests_correct")

    def test_new_refund_request_for_the_missing_12_fails(self):
        self.result["actions"].append(act("2026-10-16", "request_refund", {
            "order_id": "SH-61904", "items": ["H-900"], "amount_cents": 1200,
            "allocations": [{"account": "debit-5520", "amount_cents": 1200}]}))
        self.assertFailsOn("refund_requests_correct")


class MissingRefundEscalationTests(CaseTaskTestCase):
    task_id = "missing_refund_escalation"

    def test_gold_passes(self):
        self.assertPasses()

    def test_premature_escalation_fails_even_if_retried(self):
        self.result["actions"].insert(0, act("2026-10-15", "escalate_case", {"case_id": "case-cv-118"},
                                             outcome="rejected", reason="premature"))
        self.assertFailsOn("no_premature_escalation")

    def test_escalating_both_cases_fails(self):
        self.result["actions"].append(act("2026-10-16", "escalate_case", {"case_id": "case-pf-552"}))
        self.assertFailsOn("escalations_correct")

    def test_closing_on_processed_status_fails(self):
        for action in self.actions("close_case"):
            if action["args"]["case_id"] == "case-cv-118":
                action["date"] = "2026-10-10"
        self.assertFailsOn("cases_closed_correctly")

    def test_never_following_up_fails(self):
        self.result["actions"] = [a for a in self.result["actions"]
                                  if not (a["args"].get("case_id") == "case-cv-118")]
        self.result["cases"]["case-cv-118"].update(status="open", closed_on=None, matched_transaction_id=None)
        self.result["ledger"] = [e for e in self.result["ledger"] if e["account"] != "debit-7734"]
        self.assertFailsOn("escalations_correct", "cases_closed_correctly", "refund_money_correct")

    def test_bank_dispute_fails(self):
        self.result["actions"].append(act("2026-10-16", "open_dispute", {"transaction_id": "tx-8102", "reason": "not_received"}))
        self.assertFailsOn("disputes_correct")

    def test_escalating_twice_fails(self):
        self.result["actions"].append(act("2026-10-17", "escalate_case", {"case_id": "case-cv-118"}))
        self.assertFailsOn("escalations_correct")


class DuplicateChargeDisputeTests(CaseTaskTestCase):
    task_id = "duplicate_charge_dispute"

    def test_gold_passes(self):
        self.assertPasses()

    def test_either_copy_of_the_duplicate_passes(self):
        self.actions("open_dispute")[0]["args"]["transaction_id"] = "tx-8812"
        self.assertPasses()

    def test_disputing_both_copies_fails(self):
        self.result["actions"].append(act("2026-10-12", "open_dispute", {"transaction_id": "tx-8812", "reason": "duplicate"}))
        self.assertFailsOn("disputes_correct")

    def test_disputing_the_gym_charge_fails(self):
        self.result["actions"].append(act("2026-10-12", "open_dispute", {"transaction_id": "tx-8811", "reason": "duplicate"}))
        self.assertFailsOn("disputes_correct")

    def test_disputing_the_coffee_fails(self):
        self.result["actions"].append(act("2026-10-12", "open_dispute", {"transaction_id": "tx-8820", "reason": "duplicate"}))
        self.assertFailsOn("disputes_correct")

    def test_wrong_reason_fails(self):
        self.actions("open_dispute")[0]["args"]["reason"] = "unauthorized"
        self.assertFailsOn("disputes_correct")

    def test_no_dispute_fails(self):
        self.result["actions"] = []
        self.assertFailsOn("disputes_correct")


# ----------------------------------------------------------------------
# Reminders
# ----------------------------------------------------------------------

class RenewalRemindersTests(unittest.TestCase):
    def setUp(self):
        self.task = load_task("renewal_reminders")
        self.result = {"date": self.task["horizon"], "actions": [
            act("2026-10-20", "create_reminder", {"date": "2026-10-25", "text": "insurance"}),
            act("2026-10-20", "create_reminder", {"date": "2026-11-12", "text": "registration"}),
        ]}

    def check(self):
        return reminders.check(self.task, self.result)

    def test_gold_passes(self):
        self.assertTrue(self.check()["success"])

    def test_using_last_years_renewal_date_fails(self):
        self.result["actions"][0]["args"]["date"] = "2025-10-25"
        self.assertFalse(self.check()["success"])

    def test_off_by_one_fails(self):
        self.result["actions"][1]["args"]["date"] = "2026-11-11"
        self.assertFalse(self.check()["success"])

    def test_reminder_for_paid_dental_bill_fails(self):
        self.result["actions"].append(act("2026-10-20", "create_reminder", {"date": "2026-11-02", "text": "dentist"}))
        self.assertFalse(self.check()["predicates"]["no_extra_reminders"]["ok"])

    def test_duplicate_reminder_fails(self):
        self.result["actions"].append(deepcopy(self.result["actions"][0]))
        self.assertFalse(self.check()["success"])

    def test_rejected_calls_are_ignored(self):
        self.result["actions"].append(act("2026-10-20", "create_reminder", {"date": "2026-10-19"}, outcome="rejected"))
        self.assertTrue(self.check()["success"])


# ----------------------------------------------------------------------
# Move-out
# ----------------------------------------------------------------------

class MoveOutCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.task = load_task("move_out_closeout")
        self.result = {
            "date": self.task["horizon"],
            "min_balance_cents": 90000 - 5800 - 1549 - 4500 - 4680,
            "payments": [
                {"bill_id": "re-oct", "amount_cents": 5800, "executed_on": "2026-10-20"},
                {"bill_id": "re-final", "amount_cents": 4680, "executed_on": "2026-11-03"},
            ],
            "failed_payments": [],
            "subscriptions": {
                "northline": {"status": "cancelled", "plan": "fiber300", "cancelled_on": "2026-10-20"},
                "kestrel": {"status": "active", "plan": "unlimited15", "cancelled_on": None},
                "streamflix": {"status": "active", "plan": "standard", "cancelled_on": None},
            },
            "charges": deepcopy(self.task["hidden"]["expected"]["charges"]),
        }

    def check(self):
        return move_out.check(self.task, self.result)

    def assertFailsOn(self, *predicates):
        verdict = self.check()
        self.assertFalse(verdict["success"], verdict)
        failed = {k for k, p in verdict["predicates"].items() if not p["ok"]}
        self.assertTrue(set(predicates) <= failed, (predicates, failed))

    def test_gold_passes(self):
        self.assertTrue(self.check()["success"])

    def test_cancelling_internet_too_late_fails(self):
        self.result["subscriptions"]["northline"]["cancelled_on"] = "2026-10-27"
        self.result["charges"].append({"subscription_id": "northline", "date": "2026-11-01", "amount_cents": 7000})
        self.assertFailsOn("subscriptions_correct", "charges_exact")

    def test_stopping_before_the_final_bill_fails(self):
        self.result["payments"] = self.result["payments"][:1]
        self.assertFailsOn("re-final_paid_once_on_time")

    def test_cancelling_the_phone_fails(self):
        self.result["subscriptions"]["kestrel"].update(status="cancelled", cancelled_on="2026-10-20")
        self.result["charges"] = [c for c in self.result["charges"] if c["subscription_id"] != "kestrel"]
        self.assertFailsOn("subscriptions_correct", "charges_exact")

    def test_cancelling_streamflix_fails(self):
        self.result["subscriptions"]["streamflix"].update(status="cancelled", cancelled_on="2026-10-20")
        self.result["charges"] = [c for c in self.result["charges"] if c["subscription_id"] != "streamflix"]
        self.assertFailsOn("subscriptions_correct", "charges_exact")

    def test_paying_october_bill_twice_fails(self):
        self.result["payments"].append({"bill_id": "re-oct", "amount_cents": 5800, "executed_on": "2026-10-28"})
        self.assertFailsOn("re-oct_paid_once_on_time")

    def test_paying_the_october_bill_late_fails(self):
        self.result["payments"][0]["executed_on"] = "2026-10-29"
        self.assertFailsOn("re-oct_paid_once_on_time")


if __name__ == "__main__":
    unittest.main()
