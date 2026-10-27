# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

from datetime import timedelta

import frappe
from frappe.utils import add_days, cint, flt, getdate, now_datetime, today

from upande_casuals.pay_engine import compute_booking
from upande_casuals.workflow import get_live_workflow, paid_states

DAY_FIELDS = (
	"first_punch",
	"last_punch",
	"punch_count",
	"elapsed_hours",
	"paid_hours",
	"gate_pass",
	"day_status",
	"amount",
)
PROTECTED_STATUSES = ("Left", "Suspended")


def get_settings():
	return frappe.get_cached_doc("Casual Settings")


def business_date(settings=None):
	"""Today's working date: before the cut-off hour we are still on yesterday."""
	settings = settings or get_settings()
	return getdate(now_datetime() - timedelta(hours=cint(settings.day_cutoff_hour)))


# ---------------------------------------------------------------------------
# Pay recomputation
# ---------------------------------------------------------------------------


def recompute_open_bookings():
	settings = get_settings()
	names = frappe.get_all(
		"Casual Booking",
		filters={"docstatus": 0, "from_date": ["<=", today()], "to_date": [">=", add_days(today(), -3)]},
		pluck="name",
	)
	for name in names:
		try:
			doc = frappe.get_doc("Casual Booking", name)
			before = _day_snapshot(doc)
			compute_booking(doc, settings)
			if _day_snapshot(doc) != before:
				doc.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Casual Booking recompute failed: {name}",
				reference_doctype="Casual Booking",
				reference_name=name,
			)


def _day_snapshot(doc):
	return [tuple(str(row.get(f) or "") for f in DAY_FIELDS) for row in doc.days]


def update_overdue_flags():
	now = now_datetime()
	paid = paid_states(get_live_workflow("Casual Booking"))
	rows = frappe.get_all(
		"Casual Booking",
		filters={"docstatus": ["<", 2]},
		fields=["name", "pay_due_at", "workflow_state", "is_overdue"],
	)
	for row in rows:
		overdue = int(bool(row.pay_due_at and now > row.pay_due_at and row.workflow_state not in paid))
		if overdue != cint(row.is_overdue):
			frappe.db.set_value("Casual Booking", row.name, "is_overdue", overdue, update_modified=False)


# ---------------------------------------------------------------------------
# Employee Active / Inactive toggle
# ---------------------------------------------------------------------------


def _booked_casuals(settings, casual=None):
	bdate = business_date(settings)
	filters = {
		"docstatus": ["<", 2],
		"from_date": ["<=", add_days(bdate, cint(settings.activate_days_before))],
		"to_date": [">=", bdate],
	}
	if casual:
		filters["casual"] = casual
	return set(frappe.get_all("Casual Booking", filters=filters, pluck="casual", distinct=True))


def _apply_status(employee, current_status, should_be_active):
	if not employee or current_status in PROTECTED_STATUSES:
		return
	target = "Active" if should_be_active else "Inactive"
	if current_status != target:
		frappe.db.set_value("Employee", employee, "status", target)


def daily_status_toggle():
	"""Despite the name (kept from the brief), this runs hourly on the business date."""
	settings = get_settings()
	if not cint(settings.enable_status_toggle):
		return

	booked = _booked_casuals(settings)
	casuals = frappe.get_all(
		"On-Call Casual",
		filters={"employee": ["is", "set"]},
		fields=["name", "employee"],
	)
	statuses = dict(
		frappe.get_all(
			"Employee",
			filters={"name": ["in", [c.employee for c in casuals] or [""]]},
			fields=["name", "status"],
			as_list=True,
		)
	)
	for c in casuals:
		_apply_status(c.employee, statuses.get(c.employee), c.name in booked)


def toggle_status_for(casual):
	settings = get_settings()
	if not (casual and cint(settings.enable_status_toggle)):
		return
	employee = frappe.db.get_value("On-Call Casual", casual, "employee")
	if not employee:
		return
	status = frappe.db.get_value("Employee", employee, "status")
	_apply_status(employee, status, casual in _booked_casuals(settings, casual))


# ---------------------------------------------------------------------------
# Casual stats
# ---------------------------------------------------------------------------


def refresh_casual_stats(casual):
	if not casual or not frappe.db.exists("On-Call Casual", casual):
		return

	# Rating fields store a 0-1 fraction; the casual shows stars out of 5.
	avg_rating = frappe.db.sql(
		"""select avg(rating) from `tabCasual Booking`
		where casual = %s and docstatus < 2 and rating > 0""",
		casual,
	)[0][0]

	worked = frappe.db.sql(
		"""select count(*), max(d.work_date)
		from `tabCasual Booking Day` d
		join `tabCasual Booking` b on b.name = d.parent and d.parenttype = 'Casual Booking'
		where b.casual = %s and b.docstatus < 2
			and (case when d.manual_amount != 0 or ifnull(d.override_reason, '') != ''
				then d.manual_amount else d.amount end) > 0""",
		casual,
	)[0]

	frappe.db.set_value(
		"On-Call Casual",
		casual,
		{
			"rating": flt(flt(avg_rating) * 5, 1),
			"days_worked": cint(worked[0]),
			"last_engagement": worked[1],
		},
		update_modified=False,
	)
