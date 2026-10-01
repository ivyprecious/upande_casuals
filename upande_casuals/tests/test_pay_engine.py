# Copyright (c) 2026, ivyprecious and Contributors
# See license.txt

from datetime import datetime

from frappe.tests import UnitTestCase

from upande_casuals.pay_engine import PAY_FULL, PAY_NOTHING, compute_day

DAY = datetime(2026, 10, 1)


def t(hhmm):
	h, m = map(int, hhmm.split(":"))
	return DAY.replace(hour=h, minute=m)


def run(punches, gate_pass=False, rate=800, **kw):
	return compute_day([t(p) for p in punches], rate, 9, 1, gate_pass, **kw)


class TestComputeDay(UnitTestCase):
	def check(self, punches, gate_pass, status, amount, rate=800):
		result = run(punches, gate_pass, rate)
		self.assertEqual(result["day_status"], status, punches)
		self.assertEqual(result["amount"], amount, punches)
		return result

	def test_full_day(self):
		r = self.check(["08:00", "17:00"], False, "Full", 800)
		self.assertEqual(r["paid_hours"], 8)
		self.assertEqual(r["elapsed_hours"], 9)

	def test_over_nine_hours_is_capped(self):
		r = self.check(["08:00", "19:30"], False, "Full", 800)
		self.assertEqual(r["paid_hours"], 8)

	def test_partial_whole_hours(self):
		r = self.check(["08:00", "15:00"], False, "Partial", 600)
		self.assertEqual(r["paid_hours"], 6)

	def test_partial_minutes(self):
		r = self.check(["08:00", "15:40"], False, "Partial", 667)
		self.assertAlmostEqual(r["paid_hours"], 6.667, places=3)

	def test_duplicate_punch_counts_once(self):
		r = self.check(["08:00", "08:00", "17:00"], False, "Full", 800)
		self.assertEqual(r["punch_count"], 2)

	def test_single_punch(self):
		self.check(["08:00"], False, "Incomplete", 0)

	def test_single_punch_gate_pass(self):
		self.check(["08:00"], True, "Gate Pass", 800)

	def test_short_day_gate_pass(self):
		self.check(["08:00", "13:00"], True, "Gate Pass", 800)

	def test_no_punch(self):
		r = self.check([], False, "No Punch", 0)
		self.assertIsNone(r["first_punch"])

	def test_partial_custom_rate(self):
		self.check(["08:00", "15:00"], False, "Partial", 750, rate=1000)

	def test_under_break(self):
		r = self.check(["08:00", "08:30"], False, "Partial", 0)
		self.assertEqual(r["paid_hours"], 0)

	def test_unordered_punches(self):
		r = self.check(["17:00", "12:00", "08:00"], False, "Full", 800)
		self.assertEqual(r["first_punch"], t("08:00"))
		self.assertEqual(r["last_punch"], t("17:00"))

	def test_half_up_rounding(self):
		# 1000 * 4.5h / 8h = 562.5 -> 563 (banker's rounding would give 562)
		r = compute_day([t("08:00"), t("13:30")], 1000, 9, 1, False)
		self.assertEqual(r["amount"], 563)

	def test_rounding_off(self):
		r = compute_day([t("08:00"), t("15:40")], 800, 9, 1, False, rounding=False)
		self.assertEqual(r["amount"], 666.67)

	def test_policy_pay_nothing(self):
		r = run(["08:00", "15:00"], short_day_policy=PAY_NOTHING)
		self.assertEqual((r["day_status"], r["amount"]), ("Partial", 0))

	def test_policy_pay_full(self):
		r = run(["08:00", "15:00"], short_day_policy=PAY_FULL)
		self.assertEqual((r["day_status"], r["amount"]), ("Partial", 800))

	def test_policy_does_not_affect_full_or_incomplete(self):
		self.assertEqual(run(["08:00", "17:00"], short_day_policy=PAY_NOTHING)["amount"], 800)
		self.assertEqual(run(["08:00"], short_day_policy=PAY_FULL)["amount"], 0)
