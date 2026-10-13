"""Checker tests for conflicting_notices, replace_schedule and batch_recovery.

Each trace runs through the harness on a fresh WorkspaceV2, is replayed, and
is scored by the task's own checker. Expected verdicts come from the specs.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from benchmark.floor_policies import all_floor_policies, reference_policy
from lifeadmin.harness import run_trial

TASKS = ROOT / "benchmark/tasks"


def steps(*calls):
    """A scripted agent from (tool, args) pairs; a bare tool name means no arguments."""

    def agent(view, call_tool):
        for item in calls:
            name, args = (item, {}) if isinstance(item, str) else item
            call_tool(name, args)
        return "end_turn"

    return agent


def pay(bill, amount=None):
    return ("pay_bill", {"bill_id": bill, **({"amount_cents": amount} if amount else {})})


def schedule(bill, date, amount=None):
    return ("schedule_payment", {"bill_id": bill, "payment_date": date, **({"amount_cents": amount} if amount else {})})


def cancel(**target):
    return ("cancel_scheduled_payment", target)


class TaskTestCase(unittest.TestCase):
    task_id = None

    def run_agent(self, agent):
        record = run_trial(TASKS / f"{self.task_id}.json", agent)
        self.assertTrue(record["replay_ok"], "replay must reproduce S_stop and S_h")
        return record["verdict"]

    def expect(self, cases):
        for name, agent, success, failing in cases:
            with self.subTest(trace=name):
                verdict = self.run_agent(agent)
                self.assertEqual(verdict["success"], success, verdict)
                failed = {k for k, p in verdict["predicates"].items() if not p["ok"]}
                self.assertTrue(set(failing) <= failed, (failing, failed))

    def check_floor_policies(self):
        task = json.loads((TASKS / f"{self.task_id}.json").read_text())
        self.assertTrue(self.run_agent(reference_policy(task))["success"], "reference plan must pass")
        for name, policy in all_floor_policies(task).items():
            with self.subTest(policy=name):
                expected = task["hidden"]["floor_expected"][name] == "pass"
                self.assertEqual(self.run_agent(policy)["success"], expected)


class ConflictingNoticesTests(TaskTestCase):
    task_id = "conflicting_notices"
    RENT, TUITION, CAR = "r-4c1m", "r-8w3d", "r-2k7p"

    def test_floor_policies(self):
        self.check_floor_policies()

    def test_traces(self):
        oracle = [pay(self.TUITION), pay(self.CAR), schedule(self.RENT, "2026-10-06")]
        self.expect([
            ("T0 oracle", steps(*oracle), True, []),
            ("P0 no-op", steps(), False, ["settled_by_due[rent-2026-10]"]),
            ("N1 trusts the reminder, accepts the car-loan fee",
             steps(pay(self.RENT), pay(self.TUITION), schedule(self.CAR, "2026-10-06")),
             False, ["settled_by_due[car-loan-2026-10]"]),
            ("N3 rent one day before payday", steps(pay(self.TUITION), pay(self.CAR), schedule(self.RENT, "2026-10-05")),
             False, ["settled_by_due[rent-2026-10]", "no_failed_payments"]),
            ("N6 expects paying to cancel the schedule",
             steps(pay(self.TUITION), pay(self.CAR), schedule(self.RENT, "2026-10-08"), "advance_time", pay(self.RENT)),
             False, ["no_duplicate_payment"]),
            ("N7 adds grace days", steps(pay(self.TUITION), pay(self.CAR), schedule(self.RENT, "2026-10-10")),
             False, ["settled_by_due[rent-2026-10]"]),
            ("D1 pays the car loan again", steps(*oracle, pay(self.CAR)), False, ["no_duplicate_payment"]),
            ("A1 every bill on its true last day",
             steps(schedule(self.TUITION, "2026-10-02"), schedule(self.CAR, "2026-10-03"), schedule(self.RENT, "2026-10-08")),
             True, []),
            ("A2 waits for payday, pays rent by hand", steps(pay(self.TUITION), pay(self.CAR), "advance_time", pay(self.RENT)),
             True, []),
            ("A3 oracle, then advances past the end", steps(*oracle, *["advance_time"] * 4), True, []),
            ("A4 cancels its wrong first schedule",
             steps(schedule(self.RENT, "2026-10-01"), cancel(bill_id=self.RENT), *oracle), True, []),
        ])


class ReplaceScheduleTests(TaskTestCase):
    task_id = "replace_schedule"
    BILL = "bp-48213"

    def test_floor_policies(self):
        self.check_floor_policies()

    def test_traces(self):
        to_revision = ["advance_time", "advance_time"]
        self.expect([
            ("T1 oracle", steps(*to_revision, cancel(instruction_id="sp-1006"), schedule(self.BILL, "2026-10-16")), True, []),
            ("T2 no-op", steps(), False, ["paid_on_time", "paid_correct_amount"]),
            ("T4 pays the new bill, keeps the old schedule", steps(*to_revision, pay(self.BILL)),
             False, ["paid_correct_amount", "no_duplicate_payment"]),
            ("T5 adds instead of replacing", steps(*to_revision, schedule(self.BILL, "2026-10-16")),
             False, ["paid_correct_amount", "no_duplicate_payment"]),
            ("T6 replaces before the revision arrives",
             steps(cancel(instruction_id="sp-1006"), schedule(self.BILL, "2026-10-16")),
             False, ["paid_on_time", "paid_correct_amount"]),
            ("T8 cancels by bill after replacing", steps(*to_revision, schedule(self.BILL, "2026-10-17"), cancel(bill_id=self.BILL)),
             False, ["paid_on_time", "paid_correct_amount"]),
            ("V1 cancels by bill, pays now", steps(*to_revision, cancel(bill_id=self.BILL), pay(self.BILL)), True, []),
            ("V3 schedules first, then cancels the stale one",
             steps(*to_revision, schedule(self.BILL, "2026-10-17"), cancel(instruction_id="sp-1006")), True, []),
        ])


class BatchRecoveryTests(TaskTestCase):
    task_id = "batch_recovery"
    E, I, P, G = "r-482", "r-713", "r-157", "r-936"

    def test_floor_policies(self):
        self.check_floor_policies()

    def test_traces(self):
        self.expect([
            ("T0 oracle", steps(schedule(self.E, "2026-10-15"), schedule(self.I, "2026-10-15"), "advance_time"), True, []),
            ("T1 no-op", steps(), False, ["settled_on_time:electric-oct", "settled_on_time:insurance-oct"]),
            ("N1 re-runs the whole batch",
             steps(*[schedule(b, "2026-10-15") for b in (self.E, self.P, self.G, self.I)]),
             False, ["not_overpaid:phone-oct", "not_overpaid:gas-oct"]),
            ("N3 retries before payday", steps(pay(self.E), schedule(self.E, "2026-10-14"), schedule(self.I, "2026-10-14")),
             False, ["no_agent_failures", "settled_on_time:electric-oct"]),
            ("N5 pays on top of its own pending retry",
             steps(schedule(self.E, "2026-10-16"), schedule(self.I, "2026-10-20"), "advance_time", pay(self.E)),
             False, ["not_overpaid:electric-oct"]),
            ("N7 adds the late fee", steps("advance_time", pay(self.E, 14500), pay(self.I)),
             False, ["not_overpaid:electric-oct"]),
            ("A1 tries, waits for payday, pays", steps(pay(self.E), pay(self.I), "advance_time", pay(self.I), pay(self.E)),
             True, []),
            ("A2 each retry on its deadline", steps(schedule(self.E, "2026-10-16"), schedule(self.I, "2026-10-20")), True, []),
            ("A7 splits the insurance payment",
             steps(pay(self.I, 3500), schedule(self.I, "2026-10-15", 500), schedule(self.E, "2026-10-15")), True, []),
        ])



class DuplicateInvoiceTests(TaskTestCase):
    task_id = "duplicate_invoice"
    WATER_EBILL, WATER_PAPER, MOBILE, INTERNET = "bp-q4f7", "bp-m8c3", "bp-2hx9", "bp-z5v1"

    def test_floor_policies(self):
        self.check_floor_policies()

    def test_traces(self):
        oracle = [pay(self.MOBILE), pay(self.INTERNET), schedule(self.WATER_EBILL, "2026-10-15")]
        self.expect([
            ("O oracle", steps(*oracle), True, []),
            ("N no-op", steps(), False, ["paid_on_time[OB-W2291]", "paid_on_time[OB-NL5531]", "paid_on_time[OB-NL9140]"]),
            ("M1 pays all four records", steps(pay(self.WATER_EBILL), pay(self.WATER_PAPER), pay(self.MOBILE), pay(self.INTERNET)),
             False, ["no_duplicate_settlement"]),
            ("M2 dedupes look-alike rows", steps(pay(self.WATER_EBILL), pay(self.MOBILE)),
             False, ["paid_on_time[OB-NL9140]"]),
            ("M3 pays the other water record later", steps(*oracle, "advance_time", pay(self.WATER_PAPER)),
             False, ["no_duplicate_settlement"]),
            ("M5 expects paying to cancel the schedule",
             steps(schedule(self.WATER_EBILL, "2026-10-15"), pay(self.WATER_EBILL), pay(self.MOBILE), pay(self.INTERNET)),
             False, ["no_duplicate_settlement"]),
            ("M6 one payment day for everything",
             steps(schedule(self.WATER_EBILL, "2026-10-15"), schedule(self.MOBILE, "2026-10-15"), schedule(self.INTERNET, "2026-10-15")),
             False, ["paid_on_time[OB-NL5531]", "paid_on_time[OB-NL9140]"]),
            ("A1 the other copy, all scheduled",
             steps(schedule(self.MOBILE, "2026-10-12"), schedule(self.INTERNET, "2026-10-12"), schedule(self.WATER_PAPER, "2026-10-15")),
             True, []),
            ("A2 schedules both copies, cancels one",
             steps(schedule(self.WATER_EBILL, "2026-10-15"), schedule(self.WATER_PAPER, "2026-10-15"),
                   cancel(bill_id=self.WATER_PAPER), pay(self.MOBILE), pay(self.INTERNET)), True, []),
            ("A3 pays now, then verifies", steps(pay(self.WATER_PAPER), pay(self.MOBILE), pay(self.INTERNET), *["advance_time"] * 3),
             True, []),
        ])


class ScheduleWindowTests(TaskTestCase):
    task_id = "schedule_window"
    RENT, TUITION = "rec-4f06", "rec-d713"

    def test_floor_policies(self):
        self.check_floor_policies()

    def test_traces(self):
        oracle = [pay(self.RENT), schedule(self.TUITION, "2026-10-21")]
        self.expect([
            ("O1 oracle", steps(*oracle), True, []),
            ("P0 no-op", steps(), False, ["tuition_set_up_in_advance", "tuition_paid_in_window", "rent_paid_on_time"]),
            ("N2 waits for the refund, then pays", steps(pay(self.RENT), "advance_time", pay(self.TUITION)),
             False, ["tuition_set_up_in_advance"]),
            ("N3 waits for the refund, then schedules", steps(pay(self.RENT), "advance_time", schedule(self.TUITION, "2026-10-21")),
             False, ["tuition_set_up_in_advance"]),
            ("N4 anchors on the statement's due date", steps(pay(self.RENT), schedule(self.TUITION, "2026-10-31")),
             False, ["tuition_set_up_in_advance", "tuition_paid_in_window"]),
            ("N5 one day early", steps(pay(self.RENT), schedule(self.TUITION, "2026-10-19")),
             False, ["tuition_set_up_in_advance", "tuition_paid_in_window", "tuition_not_before_refund"]),
            ("N6 schedules, then also pays now", steps(*oracle, pay(self.TUITION)),
             False, ["tuition_paid_in_window", "tuition_not_before_refund"]),
            ("N7 schedules the refund amount", steps(pay(self.RENT), schedule(self.TUITION, "2026-10-21", 150000)),
             False, ["tuition_set_up_in_advance", "tuition_paid_in_window"]),
            ("N8 forgets rent", steps(schedule(self.TUITION, "2026-10-22")), False, ["rent_paid_on_time"]),
            ("N9 second thoughts", steps(*oracle, cancel(bill_id=self.TUITION)),
             False, ["tuition_set_up_in_advance", "tuition_paid_in_window"]),
            ("A1 deadline days, then verifies",
             steps(schedule(self.RENT, "2026-10-15"), schedule(self.TUITION, "2026-10-25"), *["advance_time"] * 5), True, []),
            ("A2 refund day, explicit amount", steps(schedule(self.TUITION, "2026-10-20", 120000), pay(self.RENT)), True, []),
            ("A3 corrects an early schedule",
             steps(schedule(self.TUITION, "2026-10-18"), cancel(bill_id=self.TUITION), schedule(self.TUITION, "2026-10-21"), pay(self.RENT)),
             True, []),
        ])


if __name__ == "__main__":
    unittest.main()
