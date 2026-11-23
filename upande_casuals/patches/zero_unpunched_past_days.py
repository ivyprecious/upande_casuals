# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

"""No punches, no pay: past days on draft bookings that have no punches become
No Punch with 0 hours and 0 pay, then their bookings' totals are recomputed.

Only drafts: submitted and cancelled bookings are approved or closed records and
are not rewritten. Days with an HR manual amount are left alone. Safe to run
again: it only touches rows that are not already No Punch / 0 / 0.
"""

import frappe
from frappe.utils import today


def execute():
	rows = frappe.db.sql(
		"""
		select d.name, d.parent
		from `tabCasual Booking Day` d
		join `tabCasual Booking` b on b.name = d.parent
		where b.docstatus = 0
			and d.work_date < %(today)s
			and d.first_punch is null and d.last_punch is null
			and ifnull(d.manual_amount, 0) = 0
			and not (d.day_status = 'No Punch' and ifnull(d.paid_hours, 0) = 0 and ifnull(d.amount, 0) = 0)
		""",
		{"today": today()},
		as_dict=True,
	)
	if not rows:
		return

	frappe.db.sql(
		"""
		update `tabCasual Booking Day`
		set day_status = 'No Punch', punch_count = 0, elapsed_hours = 0, paid_hours = 0, amount = 0
		where name in %(names)s
		""",
		{"names": tuple(r.name for r in rows)},
	)

	# Totals only, without a full save: validate on old drafts can fail for unrelated
	# reasons (e.g. overlap rules added later) and must not block the fix.
	for parent in sorted({r.parent for r in rows}):
		doc = frappe.get_doc("Casual Booking", parent)
		doc.set_totals()
		frappe.db.set_value(
			"Casual Booking",
			parent,
			{"total_hours": doc.total_hours, "days_paid": doc.days_paid, "total_amount": doc.total_amount},
			update_modified=False,
		)
