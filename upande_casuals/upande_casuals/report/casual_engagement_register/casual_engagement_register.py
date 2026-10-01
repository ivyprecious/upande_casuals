# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not (filters.from_date and filters.to_date):
		frappe.throw(_("From Date and To Date are required."))
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "work_date", "label": _("Work Date"), "fieldtype": "Date", "width": 100},
		{
			"fieldname": "casual",
			"label": _("Casual"),
			"fieldtype": "Link",
			"options": "On-Call Casual",
			"width": 110,
		},
		{"fieldname": "casual_name", "label": _("Casual Name"), "fieldtype": "Data", "width": 160},
		{
			"fieldname": "employee",
			"label": _("Employee"),
			"fieldtype": "Link",
			"options": "Employee",
			"width": 100,
		},
		{
			"fieldname": "department",
			"label": _("Department"),
			"fieldtype": "Link",
			"options": "Department",
			"width": 130,
		},
		{
			"fieldname": "designation",
			"label": _("Role"),
			"fieldtype": "Link",
			"options": "Designation",
			"width": 120,
		},
		{
			"fieldname": "task",
			"label": _("Task"),
			"fieldtype": "Link",
			"options": "Casual Task",
			"width": 120,
		},
		{"fieldname": "event_name", "label": _("Event"), "fieldtype": "Data", "width": 140},
		{"fieldname": "first_punch", "label": _("First Punch"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "last_punch", "label": _("Last Punch"), "fieldtype": "Datetime", "width": 150},
		{
			"fieldname": "elapsed_hours",
			"label": _("Elapsed Hours"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 90,
		},
		{
			"fieldname": "paid_hours",
			"label": _("Paid Hours"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 90,
		},
		{"fieldname": "day_status", "label": _("Day Status"), "fieldtype": "Data", "width": 100},
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "width": 110},
		{
			"fieldname": "booking",
			"label": _("Booking"),
			"fieldtype": "Link",
			"options": "Casual Booking",
			"width": 140,
		},
		{"fieldname": "workflow_state", "label": _("Workflow State"), "fieldtype": "Data", "width": 120},
		{"fieldname": "pay_due_at", "label": _("Pay Due At"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "is_overdue", "label": _("Overdue"), "fieldtype": "Check", "width": 70},
		{
			"fieldname": "payment_method",
			"label": _("Payment Method"),
			"fieldtype": "Link",
			"options": "Mode of Payment",
			"width": 120,
		},
		{
			"fieldname": "payment_reference",
			"label": _("Payment Reference"),
			"fieldtype": "Data",
			"width": 130,
		},
		{"fieldname": "paid_on", "label": _("Paid On"), "fieldtype": "Datetime", "width": 150},
	]


def get_data(filters):
	conditions = ["b.docstatus < 2", "d.work_date between %(from_date)s and %(to_date)s"]
	for field in ("casual", "staffing_request", "department", "designation", "task", "workflow_state"):
		if filters.get(field):
			conditions.append(f"b.{field} = %({field})s")

	return frappe.db.sql(
		f"""
		select
			d.work_date, b.casual, b.casual_name, b.employee, b.department, b.designation,
			b.task, b.event_name, d.first_punch, d.last_punch, d.elapsed_hours, d.paid_hours,
			d.day_status,
			case when d.manual_amount != 0 or ifnull(d.override_reason, '') != ''
				then d.manual_amount else d.amount end as amount,
			b.name as booking, b.workflow_state, b.pay_due_at, b.is_overdue,
			b.payment_method, b.payment_reference, b.paid_on
		from `tabCasual Booking Day` d
		join `tabCasual Booking` b on b.name = d.parent and d.parenttype = 'Casual Booking'
		where {" and ".join(conditions)}
		order by d.work_date, b.casual_name
		""",
		filters,
		as_dict=True,
	)
