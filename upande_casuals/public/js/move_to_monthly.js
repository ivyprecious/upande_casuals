// Copyright (c) 2026, ivyprecious and contributors
// For license information, please see license.txt

// Move to Monthly Casual dialog, shared by the On-Call Casual form and the Casual Dashboard.
frappe.provide("upande_casuals");

upande_casuals.MONTHLY_ROLES = [
	"HR Manager",
	"HR User",
	"HR Officer",
	"Group HR Manager",
	"System Manager",
];

upande_casuals.can_move_to_monthly = () =>
	upande_casuals.MONTHLY_ROLES.some((role) => frappe.user.has_role(role));

// casual: { name, full_name, employee, department, designation }
upande_casuals.move_to_monthly = function (casual, on_done) {
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

	const d = new frappe.ui.Dialog({
		title: __("Move to Monthly Casual"),
		fields: [
			{
				fieldname: "full_name",
				fieldtype: "Data",
				label: __("Casual"),
				read_only: 1,
				default: casual.full_name,
			},
			{
				fieldname: "employee",
				fieldtype: "Data",
				label: __("Employee"),
				read_only: 1,
				default: casual.employee,
			},
			{
				fieldname: "date_of_joining",
				fieldtype: "Date",
				label: __("Date of Joining"),
				reqd: 1,
				default: frappe.datetime.get_today(),
			},
			{
				fieldname: "department",
				fieldtype: "Link",
				options: "Department",
				label: __("Department"),
				reqd: 1,
				default: casual.department,
			},
			{
				fieldname: "designation",
				fieldtype: "Link",
				options: "Designation",
				label: __("Designation"),
				reqd: 1,
				default: casual.designation,
			},
			{
				fieldname: "note",
				fieldtype: "HTML",
				options: `<div class="alert alert-warning small" style="margin: 8px 0 0">${__(
					"This casual will be moved to the monthly payroll. Ensure a salary structure is assigned after completing this step."
				)}</div>`,
			},
		],
		primary_action_label: __("Confirm"),
		primary_action: async (values) => {
			const r = await frappe.xcall(
				"upande_casuals.api.move_to_monthly_casual",
				{
					casual_name: casual.name,
					date_of_joining: values.date_of_joining,
					department: values.department,
					designation: values.designation,
				},
				"POST",
				{ freeze: true, freeze_message: __("Moving to monthly payroll…") }
			);
			show_success(r.employee);
			on_done?.(r);
		},
		secondary_action_label: __("Cancel"),
		secondary_action: () => d.hide(),
	});

	const show_success = (employee) => {
		d.set_title(__("Moved to Monthly Payroll"));
		d.$body.html(`
			<p>✅ ${__("{0} has been moved to monthly payroll as {1}.", [
				`<b>${esc(casual.full_name)}</b>`,
				`<b>${esc(employee)}</b>`,
			])}</p>
			<p>📋 ${__(
				"Next step: Assign a salary structure to this employee before the next payroll run."
			)}</p>
			<button class="btn btn-primary btn-sm mtm-open">${__("Open Employee Record")}</button>`);
		d.$body.find(".mtm-open").on("click", () =>
			window.open(frappe.utils.get_form_link("Employee", employee), "_blank")
		);
		d.get_primary_btn().addClass("hide");
		d.get_secondary_btn().text(__("Close"));
	};

	d.show();
};
