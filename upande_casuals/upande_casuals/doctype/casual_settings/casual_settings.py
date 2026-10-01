# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class CasualSettings(Document):
	def validate(self):
		if flt(self.full_day_hours) <= flt(self.break_hours):
			frappe.throw(_("Full Day Hours must be greater than Break Hours."))
		if flt(self.break_hours) < 0:
			frappe.throw(_("Break Hours cannot be negative."))
		if not 0 <= cint(self.day_cutoff_hour) <= 23:
			frappe.throw(_("Day Cut-off Hour must be between 0 and 23."))
		if cint(self.pay_due_days_after) < 0 or cint(self.activate_days_before) < 0:
			frappe.throw(_("Day counts cannot be negative."))
		self.validate_gate_pass()

	def validate_gate_pass(self):
		# The Gate Pass doctype lives in an optional app: warn, never block.
		if not (self.enable_gate_pass and self.gate_pass_doctype):
			return
		if not frappe.db.exists("DocType", self.gate_pass_doctype):
			frappe.msgprint(
				_("DocType {0} is not installed. Gate Passes will be ignored until it is.").format(
					frappe.bold(self.gate_pass_doctype)
				),
				indicator="orange",
				alert=True,
			)
			return
		meta = frappe.get_meta(self.gate_pass_doctype)
		for fieldname in (self.gate_pass_employee_field, self.gate_pass_date_field):
			if fieldname and not meta.has_field(fieldname):
				frappe.throw(
					_("{0} has no field {1}.").format(self.gate_pass_doctype, frappe.bold(fieldname))
				)

	def on_update(self):
		frappe.clear_document_cache("Casual Settings", "Casual Settings")


@frappe.whitelist()
def seed_missing_defaults():
	frappe.only_for(("System Manager", "HR Manager"))
	from upande_casuals.setup import after_migrate

	after_migrate()
	frappe.msgprint(_("Missing defaults seeded."), alert=True, indicator="green")
