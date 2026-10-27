# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from upande_casuals.api import DAY_JOIN, EFFECTIVE_AMOUNT, _date_range, _live_booking

REPORT = "Payment Summary"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not (filters.from_date and filters.to_date):
		frappe.throw(_("From Date and To Date are required."))
	filters.from_date, filters.to_date = _date_range(filters.from_date, filters.to_date)
	return get_columns(), get_data(filters)


def get_columns():
	return [
		# Plain Data, so the ID is not shown joined to the name as a Link would be.
		{"fieldname": "employee_no", "label": _("Employee No"), "fieldtype": "Data", "width": 110},
		{"fieldname": "employee_name", "label": _("Employee Name"), "fieldtype": "Data", "width": 180},
		{
			"fieldname": "department",
			"label": _("Primary Department"),
			"fieldtype": "Link",
			"options": "Department",
			"width": 160,
		},
		{"fieldname": "days_requested", "label": _("Days Requested"), "fieldtype": "Int", "width": 120},
		{"fieldname": "days_worked", "label": _("Days Worked"), "fieldtype": "Int", "width": 110},
		{"fieldname": "total_amount", "label": _("Total Amount"), "fieldtype": "Currency", "width": 130},
		# Holds the On-Call Casual ID; the report's formatter shows it as a button.
		{"fieldname": "preview", "label": _("Preview"), "fieldtype": "Data", "width": 100},
	]


def get_data(filters):
	"""One row per casual: booked days in the period, the days that earned pay, and their total."""
	return frappe.db.sql(
		f"""
		select
			max(ifnull(c.employee, b.casual)) as employee_no,
			max(ifnull(c.full_name, b.casual_name)) as employee_name,
			max(c.department) as department,
			count(*) as days_requested,
			sum({EFFECTIVE_AMOUNT} > 0) as days_worked,
			sum({EFFECTIVE_AMOUNT}) as total_amount,
			b.casual as preview
		from {DAY_JOIN}
		left join `tabOn-Call Casual` c on c.name = b.casual
		where {_live_booking()} and d.work_date between %(from_date)s and %(to_date)s
		group by b.casual
		order by employee_name
		""",
		filters,
		as_dict=True,
	)


@frappe.whitelist()
def get_breakdown(casual, from_date, to_date):
	"""Each booked day for one casual in the period, from their Casual Booking day rows."""
	if not frappe.get_doc("Report", REPORT).is_permitted():
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	from_date, to_date = _date_range(from_date, to_date)

	days = frappe.db.sql(
		f"""
		select d.work_date, d.paid_hours, d.day_status, b.day_rate as rate,
			{EFFECTIVE_AMOUNT} as amount, b.name as booking, b.workflow_state,
			b.staffing_request, b.event_name
		from {DAY_JOIN}
		where {_live_booking()} and b.casual = %(casual)s
			and d.work_date between %(from_date)s and %(to_date)s
		order by d.work_date
		""",
		{"casual": casual, "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	requests = {}
	for d in days:
		if d.staffing_request:
			requests.setdefault(d.staffing_request, d.event_name)
	return {
		"casual": casual,
		"casual_name": frappe.db.get_value("On-Call Casual", casual, "full_name"),
		"days": days,
		"staffing_requests": [{"name": k, "event_name": v} for k, v in requests.items()],
	}
