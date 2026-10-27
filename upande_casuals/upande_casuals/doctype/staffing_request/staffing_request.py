# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate

from upande_casuals.workflow import approved_states, get_live_workflow

# Roles that may book casuals against an approved request.
BOOKING_ROLES = ("HR Manager", "HR User")
CANDIDATE_ORDER = {
	"Rating": "c.rating desc, c.last_engagement desc",
	"Least Recently Engaged": "c.last_engagement is not null, c.last_engagement asc, c.rating desc",
	"Alphabetical": "c.full_name asc",
}


class StaffingRequest(Document):
	def onload(self):
		self.set_onload("can_book_casuals", can_book_casuals(self))

	def validate(self):
		if getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("To Date cannot be before From Date."))
		if cint(self.headcount) < 1:
			frappe.throw(_("Headcount must be at least 1."))

	def on_update(self):
		if self.status != "Cancelled":
			self.create_bookings()
		self.booked_count, self.status = update_booked_count(self.name)

	def create_bookings(self):
		for row in self.calls:
			if row.outcome != "Accepted" or row.booking:
				continue
			booking = frappe.get_doc(
				{
					"doctype": "Casual Booking",
					"staffing_request": self.name,
					"casual": row.casual,
					"from_date": self.from_date,
					"to_date": self.to_date,
					"task": self.task,
					"department": self.department,
					"designation": self.designation,
				}
			).insert()
			frappe.db.set_value("Staffing Call Log", row.name, "booking", booking.name)
			row.booking = booking.name
			frappe.msgprint(
				_("Booked {0}: {1}").format(
					row.casual_name or row.casual, frappe.get_desk_link("Casual Booking", booking.name)
				),
				alert=True,
				indicator="green",
			)


def update_booked_count(name):
	"""Recount non-cancelled bookings and move the request between Open and Filled."""
	req = frappe.db.get_value("Staffing Request", name, ["headcount", "status"], as_dict=True)
	if not req:
		return 0, None
	count = frappe.db.count("Casual Booking", {"staffing_request": name, "docstatus": ["<", 2]})
	status = req.status
	if status not in ("Closed", "Cancelled"):
		status = "Filled" if count >= cint(req.headcount) else "Open"
	frappe.db.set_value(
		"Staffing Request", name, {"booked_count": count, "status": status}, update_modified=False
	)
	return count, status


@frappe.whitelist()
def get_candidates(staffing_request):
	req = frappe.get_doc("Staffing Request", staffing_request)
	req.check_permission("read")

	order = CANDIDATE_ORDER.get(
		frappe.db.get_single_value("Casual Settings", "call_list_order"), CANDIDATE_ORDER["Rating"]
	)
	return frappe.db.sql(
		f"""
		select c.name, c.full_name, c.phone, c.designation, c.day_rate, c.rating,
			c.days_worked, c.last_engagement
		from `tabOn-Call Casual` c
		left join `tabEmployee` e on e.name = c.employee
		where c.register_status = 'Available'
			and ifnull(e.status, '') not in ('Left', 'Suspended')
			and (
				c.department = %(department)s
				or exists (
					select 1 from `tabOn-Call Casual Skill` s
					where s.parent = c.name and s.parenttype = 'On-Call Casual'
						and (s.department = %(department)s or (%(designation)s != '' and s.designation = %(designation)s))
				)
				or exists (
					select 1 from `tabOn-Call Casual Department` od
					where od.parent = c.name and od.parenttype = 'On-Call Casual'
						and od.department = %(department)s
				)
			)
			and not exists (
				select 1 from `tabCasual Booking` b
				where b.casual = c.name and b.docstatus < 2
					and b.from_date <= %(to_date)s and b.to_date >= %(from_date)s
			)
		order by {order}
		""",
		{
			"department": req.department,
			"designation": req.designation or "",
			"from_date": req.from_date,
			"to_date": req.to_date,
		},
		as_dict=True,
	)


def can_book_casuals(doc, workflow=None):
	"""Whether the user can book casuals against doc: HR, and the request is in an
	approved state of the live workflow (with no workflow: not closed or cancelled)."""
	if not set(frappe.get_roles()) & set(BOOKING_ROLES):
		return False
	workflow = workflow or get_live_workflow("Staffing Request")
	if workflow:
		return doc.get(workflow.workflow_state_field) in approved_states(workflow)
	return doc.status not in ("Closed", "Cancelled")


def _booked_count(name):
	return frappe.db.count("Casual Booking", {"staffing_request": name, "docstatus": ["<", 2]})


@frappe.whitelist()
def get_booking_context(staffing_request):
	"""Everything the Book Casuals dialog shows: the request and who can be booked."""
	req = frappe.get_doc("Staffing Request", staffing_request)
	req.check_permission("read")
	can_book = can_book_casuals(req)
	return {
		"request": {
			f: req.get(f)
			for f in ("name", "event_name", "department", "designation", "from_date", "to_date", "headcount")
		},
		"booked_count": _booked_count(req.name),
		"can_book": can_book,
		"candidates": get_candidates(req.name) if can_book else [],
	}


@frappe.whitelist()
def book_casuals_for_request(staffing_request, casual_names):
	"""Create one Casual Booking per casual against an approved staffing request.

	Idempotent: a casual already booked on this request is skipped, as is anyone
	past the requested headcount or no longer a candidate (not Available, wrong
	department, or booked elsewhere on these dates). Each booking is made on its
	own, so one failure does not undo the rest.
	"""
	frappe.only_for(BOOKING_ROLES)
	if isinstance(casual_names, str):
		casual_names = frappe.parse_json(casual_names)
	req = frappe.get_doc("Staffing Request", staffing_request)
	req.check_permission("read")
	if not can_book_casuals(req):
		frappe.throw(_("Casuals can only be booked on an approved staffing request."))

	already = set(
		frappe.get_all(
			"Casual Booking",
			filters={"staffing_request": req.name, "docstatus": ["<", 2]},
			pluck="casual",
		)
	)
	candidates = {c.name for c in get_candidates(req.name)}
	remaining = cint(req.headcount) - _booked_count(req.name)

	created, skipped = [], []
	for casual in dict.fromkeys(casual_names or []):
		if casual in already:
			reason = _("Already booked on this request")
		elif remaining <= 0:
			reason = _("Fully booked")
		elif casual not in candidates:
			reason = _("Not available for this request")
		else:
			reason = None
		if reason:
			skipped.append({"casual": casual, "reason": reason})
			continue

		frappe.db.savepoint("book_casual")
		try:
			booking = frappe.get_doc(
				{
					"doctype": "Casual Booking",
					"staffing_request": req.name,
					"casual": casual,
					"from_date": req.from_date,
					"to_date": req.to_date,
					"task": req.task,
					"department": req.department,
					"designation": req.designation,
				}
			).insert()
		except Exception as e:
			frappe.db.rollback(save_point="book_casual")
			frappe.clear_last_message()
			skipped.append({"casual": casual, "reason": frappe.utils.strip_html(str(e))})
			continue
		created.append(booking.name)
		already.add(casual)
		remaining -= 1

	return {
		"count": len(created),
		"created": created,
		"skipped": skipped,
		"booked_count": _booked_count(req.name),
	}
