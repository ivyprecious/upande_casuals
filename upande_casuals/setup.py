# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

"""Install / migrate seed. Inserts missing records only and never overwrites,
because users edit settings and the workflow in the UI."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

SETTINGS = "Casual Settings"
EMPLOYMENT_TYPE = "Casual"
WORKFLOW_NAME = "Casual Booking Approval"

# State and action names are stable internal keys. Never rename them in code.
WORKFLOW_STATES = {
	"Booked": "Primary",
	"Pending Approval": "Warning",
	"Approved": "Info",
	"Paid": "Success",
	"Rejected": "Danger",
	"Cancelled": "Inverse",
}
WORKFLOW_ACTIONS = ["Submit for Approval", "Approve", "Reject", "Resubmit", "Mark Paid", "Cancel"]

DOC_STATES = [
	# state, doc_status, allow_edit
	("Booked", 0, "HR User"),
	("Pending Approval", 0, "HR Manager"),
	("Rejected", 0, "HR User"),
	("Approved", 1, "Accounts User"),
	("Paid", 1, "HR Manager"),
	("Cancelled", 2, "HR Manager"),
]
TRANSITIONS = [
	# from, action, to, role, condition
	("Booked", "Submit for Approval", "Pending Approval", "HR User", None),
	("Pending Approval", "Approve", "Approved", "HR Manager", None),
	("Pending Approval", "Reject", "Rejected", "HR Manager", None),
	("Rejected", "Resubmit", "Pending Approval", "HR User", None),
	("Approved", "Mark Paid", "Paid", "Accounts User", "doc.payment_method"),
	("Approved", "Mark Paid", "Paid", "HR Manager", "doc.payment_method"),
	("Approved", "Cancel", "Cancelled", "HR Manager", None),
]


def after_migrate():
	ensure_employment_type()
	seed_settings()
	ensure_workflow_masters()
	ensure_workflow()
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


def ensure_workflow_masters():
	for state, style in WORKFLOW_STATES.items():
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
			).insert(ignore_permissions=True)
	for action in WORKFLOW_ACTIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)


def ensure_workflow():
	# Any workflow on Casual Booking (renamed, edited, inactive) means the user owns it.
	if frappe.db.exists("Workflow", {"document_type": "Casual Booking"}):
		return

	wf = frappe.new_doc("Workflow")
	wf.update(
		{
			"workflow_name": WORKFLOW_NAME,
			"document_type": "Casual Booking",
			"workflow_state_field": "workflow_state",
			"is_active": 1,
			"send_email_alert": 0,
			# Lets the list view show the Overdue indicator over the workflow state.
			"override_status": 1,
		}
	)
	for state, doc_status, role in DOC_STATES:
		wf.append("states", {"state": state, "doc_status": str(doc_status), "allow_edit": role})
	for state, action, next_state, role, condition in TRANSITIONS:
		wf.append(
			"transitions",
			{
				"state": state,
				"action": action,
				"next_state": next_state,
				"allowed": role,
				"allow_self_approval": 1,
				"condition": condition,
			},
		)
	wf.insert(ignore_permissions=True)


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
			]
		},
		ignore_validate=True,
	)
