# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

"""Install / migrate seed. Inserts missing records only and never overwrites,
because users edit settings in the UI.

Workflows are never created or changed here: they are configured on the site
through the UI (see the note at the top of api.py)."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

SETTINGS = "Casual Settings"
EMPLOYMENT_TYPE = "Casual"


def after_migrate():
	ensure_employment_type()
	seed_settings()
	ensure_custom_fields()


def ensure_employment_type():
	if not frappe.db.exists("Employment Type", EMPLOYMENT_TYPE):
		frappe.get_doc({"doctype": "Employment Type", "employee_type_name": EMPLOYMENT_TYPE}).insert(
			ignore_permissions=True
		)


def seed_settings():
	"""Fill Casual Settings fields that have never been set.

	Unset Single fields have no row in tabSingles, which lets a deliberate 0
	(e.g. status toggle off) survive. Link values are only seeded here, never as
	JSON defaults, because the linked record may not exist when the Single is
	first saved at install.
	"""
	existing = set(
		frappe.db.sql_list(
			"select field from `tabSingles` where doctype = %s and ifnull(value, '') != ''", SETTINGS
		)
	)
	values = {
		df.fieldname: df.default
		for df in frappe.get_meta(SETTINGS).fields
		if df.default and df.fieldtype != "Link"
	}
	values["employment_type"] = (
		EMPLOYMENT_TYPE if frappe.db.exists("Employment Type", EMPLOYMENT_TYPE) else None
	)
	values["company"] = frappe.defaults.get_global_default("company")

	missing = {k: v for k, v in values.items() if v not in (None, "") and k not in existing}
	if missing:
		frappe.db.set_single_value(SETTINGS, missing)
		frappe.clear_document_cache(SETTINGS, SETTINGS)


def ensure_custom_fields():
	create_custom_fields(
		{
			"Employee": [
				{
					"fieldname": "custom_is_on_call",
					"label": "Is On-Call Casual",
					"fieldtype": "Check",
					"read_only": 1,
					"insert_after": "employment_type",
					"description": "Set by Upande Casuals. Used to exclude casuals from appraisals.",
				}
			],
			"On-Call Casual": [
				{
					"fieldname": "monthly_casual_eligible",
					"label": "Monthly Casual Eligible",
					"fieldtype": "Check",
					"default": "0",
					"read_only": 1,
					"in_list_view": 1,
					"insert_after": "register_status",
					"description": "Set daily once the casual has worked 5 or more days in a row. "
					"Never cleared automatically.",
				},
				{
					"fieldname": "moved_to_monthly",
					"label": "Moved to Monthly",
					"fieldtype": "Check",
					"default": "0",
					"read_only": 1,
					"insert_after": "monthly_casual_eligible",
				},
			],
		},
		ignore_validate=True,
	)
