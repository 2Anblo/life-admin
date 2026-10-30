"""End-to-end tests for the 12 tasks that need the v3 workspace.

Subscriptions (4), refunds and disputes (6), reminders (1) and move-out (1).
Each trace runs through the harness on a fresh LifeWorkspace, is replayed, and
is scored by the task's own checker. Every task also checks its reference plan
and the eight floor policies against ``hidden.floor_expected``.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_payment_tasks import TaskTestCase, pay, steps  # noqa: E402

ADVANCE = "advance_time"


def until(day):
    return ("advance_time", {"until": day})


def cancel_sub(sub_id):
    return ("cancel_subscription", {"subscription_id": sub_id})


def change(sub_id, plan):
    return ("change_plan", {"subscription_id": sub_id, "plan": plan})


def refund(order_id, items, allocations=None, amount=None):
    args = {"order_id": order_id, "items": list(items)}
    if amount is not None:
        args["amount_cents"] = amount
    if allocations is not None:
        args["allocations"] = [{"account": a, "amount_cents": c} for a, c in allocations]
    return ("request_refund", args)


def escalate(case_id):
    return ("escalate_case", {"case_id": case_id, "note": "Please check this refund."})


def close(case_id, matched=None):
    args = {"case_id": case_id, "resolution": "received"}
    if matched:
        args["matched_transaction_id"] = matched
    return ("close_case", args)


def dispute(tx_id, reason="duplicate"):
    return ("open_dispute", {"transaction_id": tx_id, "reason": reason})


def remind(day, text="Reminder"):
    return ("create_reminder", {"date": day, "text": text})


class LifeTaskTestCase(TaskTestCase):
    def test_floor_policies(self):
        if self.task_id:
            self.check_floor_policies()


# ----------------------------------------------------------------------
# Subscriptions
# ----------------------------------------------------------------------

class CancelUnusedSubscriptionsTests(LifeTaskTestCase):
    task_id = "cancel_unused_subscriptions"

    def test_traces(self):
        self.expect([
            ("C1 cancels music and gym now", steps(cancel_sub("tunebox"), cancel_sub("fitpath")), True, []),
            ("C2 cancels the gym app later, one day before renewal",
             steps(cancel_sub("tunebox"), ADVANCE, ADVANCE, ADVANCE, ADVANCE, until("2026-10-19"), cancel_sub("fitpath")),
             True, []),
            ("W1 also cancels the backup-only service", steps(cancel_sub("tunebox"), cancel_sub("fitpath"), cancel_sub("photovault")),
             False, ["photovault_active", "charges_exact"]),
            ("W2 cancels music after it renews", steps(ADVANCE, ADVANCE, cancel_sub("tunebox"), cancel_sub("fitpath")),
             False, ["tunebox_cancelled", "charges_exact"]),
            ("W3 cancels news read 52 days ago", steps(cancel_sub("tunebox"), cancel_sub("fitpath"), cancel_sub("dailyledger")),
             False, ["dailyledger_active"]),
            ("W4 no-op", steps(), False, ["tunebox_cancelled", "fitpath_cancelled"]),
        ])


class FreeTrialConversionTests(LifeTaskTestCase):
    task_id = "free_trial_conversion"

    def test_traces(self):
        self.expect([
            ("C1 cancels both unwanted trials now", steps(cancel_sub("snapedit"), cancel_sub("mealcrate")), True, []),
            ("W1 cancels SnapEdit inside its 2-day notice period",
             steps(until("2026-10-05"), cancel_sub("snapedit"), cancel_sub("mealcrate")),
             False, ["snapedit_cancelled", "charges_exact"]),
            ("W2 also cancels the trial the user keeps",
             steps(cancel_sub("snapedit"), cancel_sub("mealcrate"), cancel_sub("lingualoop")), False, ["lingualoop_active"]),
            ("W3 cancels FitPath because of an old trial email",
             steps(cancel_sub("snapedit"), cancel_sub("mealcrate"), cancel_sub("fitpath")), False, ["fitpath_active"]),
        ])


class PriceIncreaseReviewTests(LifeTaskTestCase):
    task_id = "price_increase_review"

    def test_traces(self):
        self.expect([
            ("C1 cancels Streamflix only", steps(cancel_sub("streamflix")), True, []),
            ("W1 cancels the annual plan by its yearly price", steps(cancel_sub("streamflix"), cancel_sub("photovault")),
             False, ["photovault_active"]),
            ("W2 cancels a plan at exactly $15.00", steps(cancel_sub("streamflix"), cancel_sub("dailyledger")),
             False, ["dailyledger_active"]),
            ("W3 cancels every price increase",
             steps(cancel_sub("streamflix"), cancel_sub("tunebox"), cancel_sub("photovault"), cancel_sub("dailyledger")),
             False, ["tunebox_active", "photovault_active", "dailyledger_active"]),
            ("W4 no-op", steps(), False, ["streamflix_cancelled", "charges_exact"]),
        ])


class DowngradeByUsageTests(LifeTaskTestCase):
    task_id = "downgrade_by_usage"

    def test_traces(self):
        self.expect([
            ("C1 changes mobile and streaming plans", steps(change("northwind", "15gb"), change("streamflix", "standard")),
             True, []),
            ("W1 sizes mobile data by September only", steps(change("northwind", "5gb"), change("streamflix", "standard")),
             False, ["northwind_active_15gb"]),
            ("W2 downgrades storage below September usage",
             steps(change("northwind", "15gb"), change("streamflix", "standard"), change("photovault", "200gb")),
             False, ["photovault_active_2tb"]),
            ("W3 changes streaming after it renews",
             steps(change("northwind", "15gb"), ADVANCE, ADVANCE, ADVANCE, change("streamflix", "standard")),
             False, ["charges_exact"]),
            ("W4 cancels instead of downgrading", steps(change("northwind", "15gb"), cancel_sub("streamflix")),
             False, ["streamflix_active_standard"]),
        ])


# ----------------------------------------------------------------------
# Refunds and disputes
# ----------------------------------------------------------------------

class RefundWithinReturnWindowTests(LifeTaskTestCase):
    task_id = "refund_within_return_window"
    LAMP = refund("BW-55120", ["L-208"], [("visa-4421", 7450)])

    def test_traces(self):
        self.expect([
            ("C1 refunds the lamp to the card", steps(self.LAMP), True, []),
            ("C2 refunds the lamp on the last allowed day", steps(until("2026-10-14"), self.LAMP), True, []),
            ("W1 refunds the whole receipt", steps(refund("BW-55120", ["L-208", "C-311"], [("visa-4421", 9250)])),
             False, ["refund_requests_correct"]),
            ("W2 accepts the store-credit default", steps(refund("BW-55120", ["L-208"])),
             False, ["refund_requests_correct", "refund_money_correct"]),
            ("W3 requests twice", steps(self.LAMP, self.LAMP), False, ["refund_requests_correct"]),
            ("W4 requests after the deadline", steps(until("2026-10-15"), self.LAMP),
             False, ["refund_requests_correct", "refund_money_correct"]),
        ])


class RefundDescriptorReconciliationTests(LifeTaskTestCase):
    task_id = "refund_descriptor_reconciliation"

    def test_traces(self):
        self.expect([
            ("C1 closes Harbor, escalates Lumen", steps(close("case-harbor-r204", "tx-5530"), escalate("case-lumen-r311")),
             True, []),
            ("W1 matches Harbor to the cashback credit", steps(close("case-harbor-r204", "tx-5512"), escalate("case-lumen-r311")),
             False, ["cases_closed_correctly"]),
            ("W2 also closes Lumen",
             steps(close("case-harbor-r204", "tx-5530"), escalate("case-lumen-r311"), close("case-lumen-r311", "tx-5541")),
             False, ["cases_closed_correctly"]),
            ("W3 escalates Harbor because its status page says processing",
             steps(escalate("case-harbor-r204"), escalate("case-lumen-r311")), False, ["escalations_correct"]),
            ("W4 no-op", steps(), False, ["escalations_correct", "cases_closed_correctly"]),
        ])


class PartialRefundWithFeeTests(LifeTaskTestCase):
    task_id = "partial_refund_with_fee"
    MOUSE = refund("PP-30918", ["X-110"], [("amex-1009", 5200)])

    def test_traces(self):
        self.expect([
            ("C1 requests price minus the opened-accessory fee", steps(self.MOUSE), True, []),
            ("W1 requests the full price", steps(refund("PP-30918", ["X-110"], [("amex-1009", 6000)])),
             False, ["refund_requests_correct"]),
            ("W2 also requests the kept monitor", steps(refund("PP-30918", ["X-110", "M-27Q"], [("amex-1009", 25200)])),
             False, ["refund_requests_correct"]),
            ("W3 accepts store credit", steps(refund("PP-30918", ["X-110"], amount=5200)),
             False, ["refund_requests_correct", "refund_money_correct"]),
            ("W4 also disputes the fee", steps(self.MOUSE, dispute("tx-7120", "incorrect_amount")),
             False, ["disputes_correct"]),
        ])


class SplitTenderRefundTests(LifeTaskTestCase):
    task_id = "split_tender_refund"
    SPLIT = refund("SH-61904", ["H-900"], [("debit-5520", 12000), ("soundhub-gift-card", 6000)])

    def test_traces(self):
        self.expect([
            ("C1 splits, escalates the short card refund, closes after the fix",
             steps(self.SPLIT, ADVANCE, ADVANCE, escalate("case-sh-4471"), ADVANCE, close("case-sh-4471")), True, []),
            ("C2 escalates one day later", steps(self.SPLIT, ADVANCE, ADVANCE, until("2026-10-17"), escalate("case-sh-4471"),
                                                 ADVANCE, close("case-sh-4471")), True, []),
            ("W1 sends all $180 to the debit card (receipt only)",
             steps(refund("SH-61904", ["H-900"], [("debit-5520", 18000)]), ADVANCE, ADVANCE, escalate("case-sh-4471"),
                   ADVANCE, close("case-sh-4471")),
             False, ["refund_requests_correct", "refund_money_correct"]),
            ("W2 escalates before the card refund posts",
             steps(self.SPLIT, ADVANCE, escalate("case-sh-4471"), ADVANCE, escalate("case-sh-4471"), ADVANCE,
                   close("case-sh-4471")),
             False, ["no_premature_escalation"]),
            ("W3 closes when the short refund posts",
             steps(self.SPLIT, ADVANCE, ADVANCE, close("case-sh-4471")),
             False, ["escalations_correct", "refund_money_correct"]),
            ("W4 escalates too late", steps(self.SPLIT, ADVANCE, ADVANCE, until("2026-10-18"), escalate("case-sh-4471"),
                                            close("case-sh-4471")),
             False, ["escalations_correct", "refund_money_correct"]),
        ])


class MissingRefundEscalationTests(LifeTaskTestCase):
    task_id = "missing_refund_escalation"

    def test_traces(self):
        self.expect([
            ("C1 closes Pinefield, escalates Corvid on the 16th, closes it after the credit",
             steps(ADVANCE, close("case-pf-552", "tx-8150"), until("2026-10-16"), escalate("case-cv-118"), ADVANCE,
                   close("case-cv-118", "tx-8171")), True, []),
            ("W1 trusts 'Processed' and closes both now",
             steps(close("case-pf-552", "tx-8107"), close("case-cv-118", "tx-8102")),
             False, ["cases_closed_correctly", "escalations_correct"]),
            ("W2 escalates Corvid before the promised date",
             steps(escalate("case-cv-118"), ADVANCE, close("case-pf-552", "tx-8150")),
             False, ["no_premature_escalation", "escalations_correct"]),
            ("W3 escalates Corvid too late",
             steps(ADVANCE, close("case-pf-552", "tx-8150"), until("2026-10-18"), escalate("case-cv-118")),
             False, ["escalations_correct", "refund_money_correct"]),
            ("W4 also escalates the settled Pinefield case",
             steps(escalate("case-pf-552"), ADVANCE, close("case-pf-552", "tx-8150"), until("2026-10-16"),
                   escalate("case-cv-118"), ADVANCE, close("case-cv-118", "tx-8171")),
             False, ["escalations_correct"]),
        ])


class DuplicateChargeDisputeTests(LifeTaskTestCase):
    task_id = "duplicate_charge_dispute"

    def test_traces(self):
        self.expect([
            ("C1 disputes the second dinner charge", steps(dispute("tx-8813")), True, []),
            ("C2 disputes the first dinner charge", steps(dispute("tx-8812")), True, []),
            ("W1 disputes both dinner charges", steps(dispute("tx-8812"), dispute("tx-8813")), False, ["disputes_correct"]),
            ("W2 disputes the gym charge for the same amount", steps(dispute("tx-8811")), False, ["disputes_correct"]),
            ("W3 also disputes the coffee", steps(dispute("tx-8813"), dispute("tx-8820")), False, ["disputes_correct"]),
            ("W4 uses the wrong reason", steps(dispute("tx-8813", "unauthorized")), False, ["disputes_correct"]),
        ])


# ----------------------------------------------------------------------
# Reminders and move-out
# ----------------------------------------------------------------------

class RenewalRemindersTests(LifeTaskTestCase):
    task_id = "renewal_reminders"

    def test_traces(self):
        self.expect([
            ("C1 sets both reminders", steps(remind("2026-10-25"), remind("2026-11-12")), True, []),
            ("W1 counts the week from the wrong day", steps(remind("2026-10-26"), remind("2026-11-12")),
             False, ["required_reminders_set", "no_extra_reminders"]),
            ("W2 also reminds about the paid dental bill", steps(remind("2026-10-25"), remind("2026-11-12"), remind("2026-11-02")),
             False, ["no_extra_reminders"]),
            ("W3 sets the registration reminder two days early", steps(remind("2026-10-25"), remind("2026-11-13")),
             False, ["required_reminders_set"]),
            ("W4 sets the insurance reminder twice", steps(remind("2026-10-25"), remind("2026-10-25"), remind("2026-11-12")),
             False, ["no_extra_reminders"]),
            ("W5 a reminder in the past is refused", steps(remind("2026-10-19"), remind("2026-11-12")),
             False, ["required_reminders_set"]),
        ])


class MoveOutCloseoutTests(LifeTaskTestCase):
    task_id = "move_out_closeout"

    def test_traces(self):
        final = [ADVANCE, ADVANCE, ADVANCE, ADVANCE, pay("re-final")]
        self.expect([
            ("C1 cancels internet, pays October now and the final bill when it arrives",
             steps(cancel_sub("northline"), pay("re-oct"), *final), True, []),
            ("W1 cancels internet after the notice deadline",
             steps(pay("re-oct"), until("2026-10-26"), cancel_sub("northline"), *final),
             False, ["subscriptions_correct", "charges_exact"]),
            ("W2 also cancels the phone plan", steps(cancel_sub("northline"), cancel_sub("kestrel"), pay("re-oct"), *final),
             False, ["subscriptions_correct", "charges_exact"]),
            ("W3 never pays the final bill", steps(cancel_sub("northline"), pay("re-oct")),
             False, ["re-final_paid_once_on_time"]),
            ("W4 tries to pay the final bill before it exists", steps(cancel_sub("northline"), pay("re-oct"), pay("re-final")),
             False, ["re-final_paid_once_on_time"]),
            ("W5 cancels Streamflix too", steps(cancel_sub("northline"), cancel_sub("streamflix"), pay("re-oct"), *final),
             False, ["subscriptions_correct"]),
        ])


if __name__ == "__main__":
    unittest.main()
