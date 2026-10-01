# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

"""Casual pay calculation.

`compute_day` is pure (no database access) so the pay rules can be unit-tested in
isolation. `compute_booking` wraps it with punch, Gate Pass and settings lookups.
"""

import math
from datetime import datetime, time, timedelta

import frappe
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime

PRO_RATA = "Pro-rata"
PAY_NOTHING = "Pay Nothing"
PAY_FULL = "Pay Full Day Rate"


def round_amount(amount, rounding=True):
	"""Round half-up. Python's `round` is banker's rounding, so it is not used."""
	if rounding:
		return math.floor(amount + 0.5)
	return math.floor(amount * 100 + 0.5) / 100


def compute_day(
	punch_times,
	day_rate,
	full_day_hours,
	break_hours,
	has_gate_pass,
	short_day_policy=PRO_RATA,
	rounding=True,
):
	"""punch_times: list of datetimes (may contain duplicates, any order).
	Returns dict: first_punch, last_punch, punch_count, elapsed_hours,
	paid_hours, day_status, amount.

	log_type is deliberately ignored: only the earliest and latest punch matter.
	"""
	punches = sorted(set(p for p in punch_times or [] if p))
	max_paid = max(full_day_hours - break_hours, 0)

	result = {
		"first_punch": punches[0] if punches else None,
		"last_punch": punches[-1] if punches else None,
		"punch_count": len(punches),
		"elapsed_hours": 0.0,
		"paid_hours": 0.0,
		"day_status": "No Punch",
		"amount": 0,
	}

	if not punches:
		return result

	if len(punches) == 1:
		if has_gate_pass:
			result.update(day_status="Gate Pass", amount=round_amount(day_rate, rounding))
		else:
			result["day_status"] = "Incomplete"
		return result

	elapsed_minutes = int((punches[-1] - punches[0]).total_seconds() // 60)
	elapsed = elapsed_minutes / 60
	paid = min(max(elapsed - break_hours, 0), max_paid)
	result.update(elapsed_hours=elapsed, paid_hours=paid)

	if max_paid and paid >= max_paid:
		result.update(day_status="Full", amount=round_amount(day_rate, rounding))
	elif has_gate_pass:
		result.update(day_status="Gate Pass", amount=round_amount(day_rate, rounding))
	else:
		if short_day_policy == PAY_FULL:
			amount = day_rate
		elif short_day_policy == PAY_NOTHING or not max_paid:
			amount = 0
		else:
			amount = day_rate * paid / max_paid
		result.update(day_status="Partial", amount=round_amount(amount, rounding))

	return result


# ---------------------------------------------------------------------------
# Database wrappers
# ---------------------------------------------------------------------------


def day_window(work_date, cutoff_hour):
	start = datetime.combine(getdate(work_date), time()) + timedelta(hours=cutoff_hour)
	return start, start + timedelta(days=1)


def get_gate_pass(employee, work_date, settings=None):
	"""Name of a submitted Gate Pass for the employee on the date, or None.

	The Gate Pass doctype comes from an optional app, so every access is guarded.
	"""
	settings = settings or frappe.get_cached_doc("Casual Settings")
	doctype = settings.gate_pass_doctype
	if not (settings.enable_gate_pass and doctype and employee):
		return None
	if not frappe.db.exists("DocType", doctype):
		return None

	meta = frappe.get_meta(doctype)
	emp_field = settings.gate_pass_employee_field or "employee"
	date_field = settings.gate_pass_date_field or "date"
	if not (meta.has_field(emp_field) and meta.has_field(date_field)):
		return None

	return frappe.db.get_value(doctype, {emp_field: employee, date_field: work_date, "docstatus": 1}, "name")


def effective_amount(row):
	return flt(row.manual_amount) if has_override(row) else flt(row.amount)


def has_override(row):
	return bool(flt(row.manual_amount) or row.override_reason)


def compute_booking(doc, settings=None):
	"""Recompute every day row of a Casual Booking from punches (in place)."""
	settings = settings or frappe.get_cached_doc("Casual Settings")
	cutoff = cint(settings.day_cutoff_hour)
	manual = settings.punch_source == "Manual"
	now = now_datetime()

	for row in doc.days:
		if has_override(row):
			continue

		start, end = day_window(row.work_date, cutoff)

		if manual:
			punches = [get_datetime(p) for p in (row.first_punch, row.last_punch) if p]
		elif start > now or not doc.employee:
			punches = []
		else:
			punches = frappe.get_all(
				"Employee Checkin",
				filters={"employee": doc.employee, "time": ["between", [start, end]]},
				pluck="time",
			)

		if start > now and not punches:
			row.update(
				{
					"first_punch": None,
					"last_punch": None,
					"punch_count": 0,
					"elapsed_hours": 0,
					"paid_hours": 0,
					"gate_pass": None,
					"day_status": "Pending",
					"amount": 0,
				}
			)
			continue

		gate_pass = get_gate_pass(doc.employee, row.work_date, settings)
		result = compute_day(
			[get_datetime(p) for p in punches],
			flt(doc.day_rate),
			flt(settings.full_day_hours),
			flt(settings.break_hours),
			bool(gate_pass),
			short_day_policy=settings.short_day_policy or PRO_RATA,
			rounding=bool(cint(settings.round_to_shilling)),
		)
		row.update(result)
		row.gate_pass = gate_pass
