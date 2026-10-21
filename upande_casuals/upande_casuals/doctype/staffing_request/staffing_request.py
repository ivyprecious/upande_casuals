# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate

CANDIDATE_ORDER = {
	"Rating": "c.rating desc, c.last_engagement desc",
	"Least Recently Engaged": "c.last_engagement is not null, c.last_engagement asc, c.rating desc",
	"Alphabetical": "c.full_name asc",
}


class StaffingRequest(Document):
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
