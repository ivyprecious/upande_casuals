# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

from datetime import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, date_diff, flt, get_time, getdate, now_datetime

from upande_casuals.pay_engine import compute_booking, effective_amount
from upande_casuals.tasks import refresh_casual_stats, toggle_status_for
from upande_casuals.workflow import get_live_workflow, paid_states

# Roles that may tick returnable items, in any workflow state before the booking is paid.
RETURNABLE_ROLES = ("HR Manager", "HOD", "Group HR Manager", "System Manager")


def booking_workflow():
	return get_live_workflow("Casual Booking")


class CasualBooking(Document):
	def before_insert(self):
		if not self.day_rate and self.casual:
			self.day_rate = frappe.db.get_value("On-Call Casual", self.casual, "day_rate")
		if not self.day_rate:
			self.day_rate = frappe.db.get_single_value("Casual Settings", "default_day_rate")

	def validate(self):
		if getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("To Date cannot be before From Date."))
		self.validate_overlap()
		self.validate_overrides()
		self.sync_days()
		settings = frappe.get_cached_doc("Casual Settings")
		self.sync_returnable_items(settings)
		if settings.punch_source == "Manual" and self.docstatus == 0:
			compute_booking(self, settings)
		self.set_pay_due(settings)
		self.set_totals()

	def validate_overlap(self):
		clash = frappe.db.get_value(
			"Casual Booking",
			{
				"casual": self.casual,
				"docstatus": ["<", 2],
				"name": ["!=", self.name or ""],
				"from_date": ["<=", self.to_date],
				"to_date": [">=", self.from_date],
			},
			["name", "from_date", "to_date"],
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("{0} is already booked from {1} to {2} on {3}.").format(
					frappe.bold(self.casual_name or self.casual),
					frappe.format(clash.from_date, "Date"),
					frappe.format(clash.to_date, "Date"),
					frappe.get_desk_link("Casual Booking", clash.name),
				),
				title=_("Overlapping Booking"),
			)

	def validate_overrides(self):
		for row in self.days:
			if flt(row.manual_amount) and not row.override_reason:
				frappe.throw(_("Row {0}: Override Reason is required for a manual amount.").format(row.idx))

	def sync_days(self):
		"""Exactly one row per date in the range, keeping rows for retained dates."""
		existing = {}
		for row in self.days:
			existing.setdefault(getdate(row.work_date), row)

		start = getdate(self.from_date)
		rows = []
		for i in range(date_diff(self.to_date, self.from_date) + 1):
			work_date = add_days(start, i)
			row = existing.get(work_date) or frappe.new_doc(
				"Casual Booking Day", parent_doc=self, parentfield="days"
			)
			row.work_date = work_date
			if not row.day_status:
				row.day_status = "Pending"
			rows.append(row)

		for idx, row in enumerate(rows, 1):
			row.idx = idx
		self.set("days", rows)

	def sync_returnable_items(self, settings=None):
		"""One row per item in Casual Settings. Ticked rows are kept even if the
		item is later removed from settings, as a record of the return."""
		settings = settings or frappe.get_cached_doc("Casual Settings")
		wanted = [r.item_name for r in settings.get("returnable_items") or [] if r.item_name]
		rows = [r for r in self.returnable_items if r.item_name in wanted or r.returned]
		have = {r.item_name for r in rows}
		for item_name in wanted:
			if item_name not in have:
				rows.append(
					frappe.new_doc(
						"Casual Booking Returnable",
						parent_doc=self,
						parentfield="returnable_items",
						item_name=item_name,
					)
				)
		for idx, row in enumerate(rows, 1):
			row.idx = idx
		self.set("returnable_items", rows)

	def pending_returns(self):
		return [r.item_name for r in self.returnable_items if not r.returned]

	def returnables_locked(self):
		return self.docstatus == 2 or self.workflow_state in paid_states(booking_workflow())

	def set_pay_due(self, settings):
		due_date = add_days(self.to_date, cint(settings.pay_due_days_after))
		self.pay_due_at = datetime.combine(
			getdate(due_date), get_time(settings.pay_cutoff_time or "11:00:00")
		)

	def set_totals(self):
		self.total_hours = sum(flt(row.paid_hours) for row in self.days)
		self.days_paid = sum(1 for row in self.days if effective_amount(row) > 0)
		self.total_amount = sum(effective_amount(row) for row in self.days)

	def before_submit(self):
		# Approving a booking submits it, so this holds for any workflow's approve action.
		if not self.payment_method:
			frappe.throw(
				_("Set a Payment Method on {0} before approving it.").format(self.name),
				title=_("Payment Method Required"),
			)

	def onload(self):
		self.set_onload("returnables_locked", self.returnables_locked())

	def before_update_after_submit(self):
		if self.workflow_state in paid_states(booking_workflow()):
			if not self.payment_method:
				frappe.throw(_("Set a Payment Method before marking the booking as paid."))
			# Bookings approved before items were added to settings get their rows here.
			self.sync_returnable_items()
			if pending := self.pending_returns():
				frappe.throw(
					_("{0} cannot be paid until these items are returned: {1}").format(
						self.name, ", ".join(pending)
					),
					title=_("Items Not Returned"),
				)
			self.paid_on = self.paid_on or now_datetime()
			self.paid_by = self.paid_by or frappe.session.user
			self.is_overdue = 0

	def on_update(self):
		self.refresh_links()

	def on_submit(self):
		self.refresh_links()

	def on_update_after_submit(self):
		self.refresh_links()

	def on_cancel(self):
		self.refresh_links()

	def after_delete(self):
		self.refresh_links()

	def refresh_links(self):
		toggle_status_for(self.casual)
		refresh_casual_stats(self.casual)
		if self.staffing_request:
			from upande_casuals.upande_casuals.doctype.staffing_request.staffing_request import (
				update_booked_count,
			)

			update_booked_count(self.staffing_request)


@frappe.whitelist()
def recalculate(name):
	doc = frappe.get_doc("Casual Booking", name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only draft bookings can be recalculated."))
	compute_booking(doc)
	doc.save()
	return doc.as_dict()


@frappe.whitelist()
def set_item_returned(name, item_name, returned):
	frappe.only_for(RETURNABLE_ROLES)
	doc = frappe.get_doc("Casual Booking", name)
	if doc.returnables_locked():
		frappe.throw(
			_("Returned items cannot be changed on a {0} booking.").format(
				_(doc.workflow_state or "Cancelled")
			)
		)
	doc.sync_returnable_items()
	row = next((r for r in doc.returnable_items if r.item_name == item_name), None)
	if not row:
		frappe.throw(_("{0} is not a returnable item on {1}.").format(item_name, name))

	returned = cint(returned)
	row.update(
		{
			"returned": returned,
			"returned_by": frappe.session.user if returned else None,
			"returned_on": now_datetime() if returned else None,
		}
	)
	# Written directly: the tick is allowed in states the user otherwise cannot edit.
	for r in doc.returnable_items:
		if r.is_new():
			r.db_insert()
		elif r.name == row.name:
			r.db_update()
	doc.add_comment(
		"Info",
		_("{0} marked {1}").format(item_name, _("Returned") if returned else _("Not Returned")),
	)
	return [r.as_dict() for r in doc.returnable_items]
