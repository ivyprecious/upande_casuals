# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, today

EMPLOYEE_COPY_FIELDS = ("first_name", "middle_name", "last_name", "gender", "date_of_birth", "department")


class OnCallCasual(Document):
	def before_insert(self):
		settings = frappe.get_cached_doc("Casual Settings")
		if not self.day_rate:
			self.day_rate = settings.default_day_rate

		# Runs before naming, so the casual's name (field:employee) is the Employee ID.
		if self.employee:
			self.copy_from_employee()
		elif cint(settings.create_employee):
			self.employee = self.create_employee(settings)
		else:
			frappe.throw(_("Select an Employee. Automatic Employee creation is off in Casual Settings."))

	def validate(self):
		self.full_name = " ".join(filter(None, (self.first_name, self.middle_name, self.last_name)))

	def after_insert(self):
		frappe.msgprint(
			_("Enrol on biometric device with user ID: {0}").format(frappe.bold(self.employee)),
			title=_("Biometric Enrolment"),
			indicator="blue",
		)

	def on_update(self):
		# Status is owned by the booking toggle and is never synced from here.
		if self.employee:
			frappe.db.set_value(
				"Employee",
				self.employee,
				{"cell_number": self.phone, "department": self.department, "designation": self.designation},
			)

	def copy_from_employee(self):
		emp = frappe.db.get_value("Employee", self.employee, EMPLOYEE_COPY_FIELDS, as_dict=True)
		if not emp:
			frappe.throw(_("Employee {0} does not exist.").format(self.employee))
		for field in EMPLOYEE_COPY_FIELDS:
			if not self.get(field) and emp.get(field):
				self.set(field, emp.get(field))
		frappe.db.set_value("Employee", self.employee, "custom_is_on_call", 1)

	def create_employee(self, settings):
		company = settings.company or frappe.defaults.get_global_default("company")
		if not company:
			frappe.throw(_("Set a Company in Casual Settings before creating casuals."))

		emp = frappe.new_doc("Employee")
		emp.update(
			{
				"naming_series": settings.employee_naming_series or "TSK-.###",
				"first_name": self.first_name,
				"middle_name": self.middle_name,
				"last_name": self.last_name,
				"gender": self.gender,
				"date_of_birth": self.date_of_birth,
				"date_of_joining": today(),
				"company": company,
				"department": self.department,
				"designation": self.designation,
				"employment_type": settings.employment_type,
				"status": "Inactive",
				"cell_number": self.phone,
				"custom_is_on_call": 1,
			}
		)
		emp.insert(ignore_permissions=True)
		if cint(settings.set_device_id):
			frappe.db.set_value("Employee", emp.name, "attendance_device_id", emp.name)
		return emp.name
