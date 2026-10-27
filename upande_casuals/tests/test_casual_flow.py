# Copyright (c) 2026, ivyprecious and Contributors
# See license.txt

from datetime import datetime, time, timedelta

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from upande_casuals import api
from upande_casuals.tasks import business_date, flag_monthly_casual_eligible, toggle_status_for
from upande_casuals.upande_casuals.doctype.casual_booking.casual_booking import (
	booking_workflow,
	recalculate,
	set_item_returned,
)
from upande_casuals.upande_casuals.doctype.staffing_request.staffing_request import (
	book_casuals_for_request,
	can_book_casuals,
	get_candidates,
)
from upande_casuals.workflow import paid_states, payable_states, settled_states

# The app never creates workflows (they are set up on each site through the UI),
# so tests bring their own. Used only when the test site has no booking workflow.
TEST_WORKFLOW_STATES = [
	# state, style, doc_status, allow_edit
	("Booked", "Primary", 0, "HR User"),
	("Pending Approval", "Warning", 0, "HR Manager"),
	("Rejected", "Danger", 0, "HR User"),
	("Approved", "Info", 1, "Accounts User"),
	("Paid", "Success", 1, "HR Manager"),
	("Cancelled", "Inverse", 2, "HR Manager"),
]
TEST_WORKFLOW_TRANSITIONS = [
	# from, action, to, role, condition
	("Booked", "Submit for Approval", "Pending Approval", "HR User", None),
	("Pending Approval", "Approve", "Approved", "HR Manager", None),
	("Pending Approval", "Reject", "Rejected", "HR Manager", None),
	("Rejected", "Resubmit", "Pending Approval", "HR User", None),
	("Approved", "Mark Paid", "Paid", "HR Manager", "doc.payment_method"),
	("Approved", "Cancel", "Cancelled", "HR Manager", None),
]


def ensure_test_workflow():
	if frappe.db.exists("Workflow", {"document_type": "Casual Booking"}):
		return
	for state, style, _doc_status, _role in TEST_WORKFLOW_STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
			).insert()
	for action in {t[1] for t in TEST_WORKFLOW_TRANSITIONS}:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert()
	wf = frappe.get_doc(
		{
			"doctype": "Workflow",
			"workflow_name": "Casual Booking Test Workflow",
			"document_type": "Casual Booking",
			"workflow_state_field": "workflow_state",
			"is_active": 1,
			"send_email_alert": 0,
		}
	)
	for state, _style, doc_status, role in TEST_WORKFLOW_STATES:
		wf.append("states", {"state": state, "doc_status": str(doc_status), "allow_edit": role})
	for state, action, next_state, role, condition in TEST_WORKFLOW_TRANSITIONS:
		wf.append(
			"transitions",
			{
				"state": state,
				"action": action,
				"next_state": next_state,
				"allowed": role,
				"allow_self_approval": 1,
				"condition": condition,
			},
		)
	wf.insert()


class TestCasualFlow(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_single_value("Casual Settings", "company")
		cls.department = frappe.db.get_value("Department", {"company": cls.company, "is_group": 0}, "name")
		cls.mode_of_payment = frappe.db.get_value("Mode of Payment", {}, "name")
		if not (cls.company and cls.department and cls.mode_of_payment):
			raise cls.skipTest(cls, "Needs a Company, Department and Mode of Payment")
		ensure_test_workflow()

	def make_casual(self, **kw):
		return frappe.get_doc(
			{
				"doctype": "On-Call Casual",
				"first_name": "Test",
				"last_name": frappe.generate_hash(length=6),
				"gender": frappe.db.get_value("Gender", {}, "name"),
				"date_of_birth": "1998-05-04",
				"phone": "0700000000",
				"mpesa_number": "0700000000",
				"national_id": frappe.generate_hash(length=8),
				"image": "/files/casual-test.png",
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

	def set_returnables(self, items):
		settings = frappe.get_doc("Casual Settings")
		settings.set("returnable_items", [{"item_name": i} for i in items])
		settings.save()

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
		]:
			booking = apply_workflow(booking, action)
			self.assertEqual(booking.workflow_state, state)

		# Approving submits the booking, which needs a payment method.
		with self.assertRaises(frappe.ValidationError):
			apply_workflow(booking, "Approve")
		booking.reload()
		booking.payment_method = self.mode_of_payment
		booking.save()
		booking = apply_workflow(booking, "Approve")
		self.assertEqual((booking.workflow_state, booking.docstatus), ("Approved", 1))

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

	def test_returnable_items_block_payment(self):
		self.set_returnables(["T-Shirt"])
		self.addCleanup(self.set_returnables, [])

		casual = self.make_casual()
		booking = self.make_booking(casual, add_days(today(), -20))
		self.assertEqual([r.item_name for r in booking.returnable_items], ["T-Shirt"])
		booking.payment_method = self.mode_of_payment
		booking.save()
		booking = apply_workflow(booking, "Submit for Approval")
		booking = apply_workflow(booking, "Approve")

		with self.assertRaises(frappe.ValidationError):
			apply_workflow(booking, "Mark Paid")

		set_item_returned(booking.name, "T-Shirt", 1)
		booking.reload()
		self.assertTrue(booking.returnable_items[0].returned)
		self.assertEqual(booking.returnable_items[0].returned_by, frappe.session.user)
		booking = apply_workflow(booking, "Mark Paid")
		self.assertEqual(booking.workflow_state, "Paid")

	def test_book_casuals_for_request(self):
		first, second = self.make_casual(), self.make_casual()
		day = add_days(business_date(), 45)
		req = frappe.get_doc(
			{
				"doctype": "Staffing Request",
				"event_name": "Test Booking Flow",
				"department": self.department,
				"from_date": day,
				"to_date": day,
				"headcount": 1,
			}
		).insert()
		if not can_book_casuals(req):
			self.skipTest("This site's Staffing Request workflow needs the request approved first")

		result = book_casuals_for_request(req.name, [first.name, second.name])
		self.assertEqual(result["count"], 1)
		self.assertEqual(result["booked_count"], 1)
		self.assertEqual([s["casual"] for s in result["skipped"]], [second.name])
		booking = frappe.get_doc("Casual Booking", result["created"][0])
		self.assertEqual(
			(booking.staffing_request, booking.casual, str(booking.from_date), booking.department),
			(req.name, first.name, str(day), self.department),
		)
		self.assertEqual(booking.mpesa_number, first.mpesa_number)

		# Idempotent: booking the same casual again changes nothing.
		again = book_casuals_for_request(req.name, [first.name])
		self.assertEqual((again["count"], again["booked_count"]), (0, 1))
		self.assertEqual(frappe.db.get_value("Staffing Request", req.name, "booked_count"), 1)

	def test_pay_casuals_reads_workflow(self):
		self.set_returnables(["T-Shirt"])
		self.addCleanup(self.set_returnables, [])

		casual = self.make_casual()
		day = add_days(today(), -25)
		booking = self.make_booking(casual, day)
		booking.payment_method = self.mode_of_payment
		booking.save()
		booking = apply_workflow(booking, "Submit for Approval")
		booking = apply_workflow(booking, "Approve")
		self.assertIn(booking.workflow_state, payable_states(booking_workflow()))

		preview = api.preview_casual_pay(day, day, [casual.name], self.mode_of_payment)
		self.assertEqual([r.name for r in preview["rows"]], [booking.name])
		self.assertTrue(preview["rows"][0].can_pay)
		self.assertEqual(preview["rows"][0].pending_returns, ["T-Shirt"])
		self.assertEqual(preview["ready"], [])

		set_item_returned(booking.name, "T-Shirt", 1)
		preview = api.preview_casual_pay(day, day, [casual.name], self.mode_of_payment)
		self.assertEqual(preview["ready"], [booking.name])

		result = api.pay_casuals(preview["ready"], self.mode_of_payment)
		self.assertEqual((result["paid"], result["failed"]), ([booking.name], []))
		booking.reload()
		self.assertIn(booking.workflow_state, paid_states(booking_workflow()))
		self.assertTrue(booking.returnables_locked())

	def test_reason_follows_danger_state(self):
		booking = self.make_booking(self.make_casual(), add_days(today(), -30))
		booking = apply_workflow(booking, "Submit for Approval")
		kinds = {a["action"]: a["kind"] for a in api._actions(booking, api._workflow("Casual Booking"))}
		danger = frappe.db.get_value("Workflow State", "Rejected", "style") == "Danger"
		self.assertEqual(kinds["Reject"], "reason" if danger else "forward")
		self.assertEqual(kinds["Approve"], "forward")
		if danger:
			with self.assertRaises(frappe.ValidationError):
				api.apply_action("Casual Booking", booking.name, "Reject")

	def test_no_returnable_items_when_settings_empty(self):
		self.set_returnables([])
		booking = self.make_booking(self.make_casual(), add_days(today(), 30))
		self.assertEqual(booking.returnable_items, [])

	def test_other_departments_make_candidates(self):
		other = frappe.db.get_value(
			"Department", {"company": self.company, "is_group": 0, "name": ["!=", self.department]}, "name"
		)
		if not other:
			self.skipTest("Needs a second Department")
		casual = self.make_casual(other_departments=[{"department": other}])
		day = add_days(business_date(), 40)
		req = frappe.get_doc(
			{
				"doctype": "Staffing Request",
				"event_name": "Other Dept",
				"department": other,
				"from_date": day,
				"to_date": day,
				"headcount": 1,
			}
		).insert()
		self.assertIn(casual.name, [c.name for c in get_candidates(req.name)])

		with self.assertRaises(frappe.ValidationError):
			self.make_casual(other_departments=[{"department": self.department}])

	def test_monthly_eligibility_needs_five_days_in_a_row(self):
		casual = self.make_casual()
		start = add_days(today(), -90)
		booking = self.make_booking(casual, start, add_days(start, 5))
		state = sorted(settled_states(booking_workflow()))[0]
		frappe.db.set_value("Casual Booking", booking.name, {"workflow_state": state, "docstatus": 1})

		def pay_days(unpaid_idx=None):
			for row in booking.days:
				amount = 0 if row.idx == unpaid_idx else 800
				frappe.db.set_value("Casual Booking Day", row.name, "amount", amount)
			flag_monthly_casual_eligible()
			return frappe.db.get_value("On-Call Casual", casual.name, "monthly_casual_eligible")

		# Six days with the third unpaid: runs of 2 and 3.
		self.assertEqual(pay_days(unpaid_idx=3), 0)
		self.assertEqual(pay_days(), 1)
		# Never cleared by the job once set.
		self.assertEqual(pay_days(unpaid_idx=3), 1)

	def test_move_to_monthly_casual(self):
		casual = self.make_casual()
		designation = frappe.db.get_value("Designation", {}, "name")
		joining = add_days(today(), -1)
		r = api.move_to_monthly_casual(casual.name, joining, self.department, designation)

		self.assertEqual(r["employee"], casual.employee)
		emp = frappe.get_doc("Employee", casual.employee)
		self.assertEqual(emp.status, "Active")
		self.assertEqual(getdate(emp.date_of_joining), getdate(joining))
		self.assertEqual(emp.designation, designation)
		self.assertEqual(emp.employment_type, api.MONTHLY_EMPLOYMENT_TYPE)
		self.assertEqual(emp.custom_is_on_call, 0)
		casual.reload()
		self.assertEqual(casual.moved_to_monthly, 1)
		self.assertEqual(casual.register_status, "Do Not Call")

		# No bookings, yet the status toggle must leave a monthly casual Active.
		toggle_status_for(casual.name)
		self.assertEqual(self.employee_status(casual), "Active")
		with self.assertRaises(frappe.ValidationError):
			api.move_to_monthly_casual(casual.name, joining, self.department, designation)
