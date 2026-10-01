# Copyright (c) 2026, ivyprecious and Contributors
# See license.txt

from datetime import datetime, time, timedelta

import frappe
from frappe.model.workflow import WorkflowTransitionError, apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from upande_casuals.tasks import business_date
from upande_casuals.upande_casuals.doctype.casual_booking.casual_booking import recalculate
from upande_casuals.upande_casuals.doctype.staffing_request.staffing_request import get_candidates


class TestCasualFlow(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_single_value("Casual Settings", "company")
		cls.department = frappe.db.get_value("Department", {"company": cls.company, "is_group": 0}, "name")
		cls.mode_of_payment = frappe.db.get_value("Mode of Payment", {}, "name")
		if not (cls.company and cls.department and cls.mode_of_payment):
			raise cls.skipTest(cls, "Needs a Company, Department and Mode of Payment")

	def make_casual(self, **kw):
		return frappe.get_doc(
			{
				"doctype": "On-Call Casual",
				"first_name": "Test",
				"last_name": frappe.generate_hash(length=6),
				"gender": frappe.db.get_value("Gender", {}, "name"),
				"date_of_birth": "1998-05-04",
				"phone": "0700000000",
				"department": self.department,
				**kw,
			}
		).insert()

	def make_booking(self, casual, from_date, to_date=None):
		return frappe.get_doc(
			{
				"doctype": "Casual Booking",
				"casual": casual.name,
				"from_date": from_date,
				"to_date": to_date or from_date,
			}
		).insert()

	def checkin(self, employee, when):
		frappe.get_doc({"doctype": "Employee Checkin", "employee": employee, "time": when}).insert()

	def employee_status(self, casual):
		return frappe.db.get_value("Employee", casual.employee, "status")

	def test_casual_creates_inactive_employee(self):
		casual = self.make_casual()
		emp = frappe.get_doc("Employee", casual.employee)
		self.assertEqual(casual.name, emp.name)
		self.assertTrue(emp.name.startswith("TSK-"))
		self.assertEqual(emp.status, "Inactive")
		self.assertEqual(emp.attendance_device_id, emp.name)
		self.assertEqual(emp.custom_is_on_call, 1)
		self.assertEqual(casual.day_rate, 800)
		self.assertEqual(casual.full_name, f"Test {casual.last_name}")

	def test_staffing_request_books_and_activates(self):
		casual = self.make_casual()
		# Business date, not calendar date: before the cut-off hour it is still yesterday.
		tomorrow = add_days(business_date(), 1)
		req = frappe.get_doc(
			{
				"doctype": "Staffing Request",
				"event_name": "Test Gala",
				"department": self.department,
				"from_date": tomorrow,
				"to_date": tomorrow,
				"headcount": 1,
			}
		).insert()

		self.assertIn(casual.name, [c.name for c in get_candidates(req.name)])

		req.append("calls", {"casual": casual.name})
		req.save()
		self.assertFalse(req.calls[0].booking)

		req.calls[0].outcome = "Accepted"
		req.save()
		booking = req.calls[0].booking
		self.assertTrue(booking)
		self.assertEqual(frappe.db.get_value("Staffing Call Log", req.calls[0].name, "booking"), booking)
		self.assertEqual(frappe.db.get_value("Staffing Request", req.name, "booked_count"), 1)
		self.assertEqual(frappe.db.get_value("Staffing Request", req.name, "status"), "Filled")
		self.assertEqual(self.employee_status(casual), "Active")
		self.assertNotIn(casual.name, [c.name for c in get_candidates(req.name)])

		# Saving again must not create a second booking.
		req.save()
		self.assertEqual(frappe.db.count("Casual Booking", {"staffing_request": req.name}), 1)

	def test_overlap_rejected(self):
		casual = self.make_casual()
		self.make_booking(casual, add_days(today(), 5), add_days(today(), 7))
		with self.assertRaises(frappe.ValidationError):
			self.make_booking(casual, add_days(today(), 7), add_days(today(), 8))

	def test_days_rebuilt(self):
		casual = self.make_casual()
		booking = self.make_booking(casual, add_days(today(), 10), add_days(today(), 12))
		self.assertEqual(
			[getdate(d.work_date) for d in booking.days],
			[getdate(add_days(today(), i)) for i in (10, 11, 12)],
		)
		self.assertTrue(all(d.day_status == "Pending" for d in booking.days))
		first_row = booking.days[1].name
		booking.from_date = add_days(today(), 11)
		booking.save()
		self.assertEqual(len(booking.days), 2)
		self.assertEqual(booking.days[0].name, first_row)

	def test_pay_from_punches_and_workflow(self):
		casual = self.make_casual()
		work_date = getdate(add_days(today(), -2))
		booking = self.make_booking(casual, work_date)
		self.assertEqual(booking.workflow_state, "Booked")
		self.assertEqual(booking.day_rate, 800)

		# Checkin validation rejects inactive employees.
		frappe.db.set_value("Employee", casual.employee, "status", "Active")
		day = datetime.combine(work_date, time())
		self.checkin(casual.employee, day.replace(hour=8))
		self.checkin(casual.employee, day.replace(hour=15))
		# Punch before the 04:00 cut-off belongs to the previous day and is ignored here.
		self.checkin(casual.employee, day.replace(hour=3))

		recalculate(booking.name)
		booking.reload()
		row = booking.days[0]
		self.assertEqual((row.day_status, row.amount, row.paid_hours), ("Partial", 600, 6))
		self.assertEqual(booking.total_amount, 600)

		# Saving a past booking re-runs the toggle, which deactivates the employee.
		self.assertEqual(self.employee_status(casual), "Inactive")
		frappe.db.set_value("Employee", casual.employee, "status", "Active")

		# A 01:30 clock-out the next morning still belongs to the work date.
		self.checkin(casual.employee, day + timedelta(days=1, hours=1, minutes=30))
		recalculate(booking.name)
		booking.reload()
		self.assertEqual((booking.days[0].day_status, booking.total_amount), ("Full", 800))

		# Manual override
		booking.days[0].manual_amount = 500
		booking.days[0].override_reason = "Left early, agreed"
		booking.save()
		self.assertEqual(booking.total_amount, 500)

		for action, state in [
			("Submit for Approval", "Pending Approval"),
			("Reject", "Rejected"),
			("Resubmit", "Pending Approval"),
			("Approve", "Approved"),
		]:
			booking = apply_workflow(booking, action)
			self.assertEqual(booking.workflow_state, state)
		self.assertEqual(booking.docstatus, 1)

		with self.assertRaises(WorkflowTransitionError):
			apply_workflow(booking, "Mark Paid")

		booking.reload()
		booking.payment_method = self.mode_of_payment
		booking.save()
		booking = apply_workflow(booking, "Mark Paid")
		self.assertEqual(booking.workflow_state, "Paid")
		self.assertTrue(booking.paid_on)
		self.assertEqual(booking.paid_by, frappe.session.user)

		booking.rating = 0.8
		booking.save()
		self.assertEqual(frappe.db.get_value("On-Call Casual", casual.name, "rating"), 4)
		self.assertEqual(frappe.db.get_value("On-Call Casual", casual.name, "days_worked"), 1)

	def test_status_toggle_respects_protected_statuses(self):
		casual = self.make_casual()
		frappe.db.set_value("Employee", casual.employee, "status", "Suspended")
		self.make_booking(casual, add_days(business_date(), 1))
		self.assertEqual(self.employee_status(casual), "Suspended")

	def test_gate_pass_pays_full_day(self):
		if not frappe.db.exists("DocType", "Gate Pass"):
			self.skipTest("upande_ta is not installed")
		casual = self.make_casual()
		work_date = getdate(add_days(today(), -3))
		booking = self.make_booking(casual, work_date)
		frappe.db.set_value("Employee", casual.employee, "status", "Active")
		self.checkin(casual.employee, datetime.combine(work_date, time(8)))

		recalculate(booking.name)
		booking.reload()
		self.assertEqual((booking.days[0].day_status, booking.days[0].amount), ("Incomplete", 0))

		# Fixture only: bypass the Gate Pass approval workflow.
		gate_pass = frappe.get_doc(
			{"doctype": "Gate Pass", "employee": casual.employee, "date": work_date, "pass_type": "Personal"}
		)
		gate_pass.docstatus = 1
		gate_pass.set_new_name()
		gate_pass.db_insert()

		recalculate(booking.name)
		booking.reload()
		row = booking.days[0]
		self.assertEqual((row.day_status, row.amount, row.gate_pass), ("Gate Pass", 800, gate_pass.name))

	def test_gate_pass_guard_without_doctype(self):
		from upande_casuals.pay_engine import get_gate_pass

		settings = frappe.get_doc("Casual Settings")
		settings.gate_pass_doctype = "No Such DocType"
		self.assertIsNone(get_gate_pass("TSK-000", today(), settings))
