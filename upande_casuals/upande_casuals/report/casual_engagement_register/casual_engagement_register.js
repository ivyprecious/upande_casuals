// Copyright (c) 2026, ivyprecious and contributors
// For license information, please see license.txt

frappe.query_reports["Casual Engagement Register"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.month_end(),
		},
		{ fieldname: "casual", label: __("Casual"), fieldtype: "Link", options: "On-Call Casual" },
		{
			fieldname: "staffing_request",
			label: __("Staffing Request"),
			fieldtype: "Link",
			options: "Staffing Request",
		},
		{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
		{ fieldname: "designation", label: __("Role"), fieldtype: "Link", options: "Designation" },
		{ fieldname: "task", label: __("Task"), fieldtype: "Link", options: "Casual Task" },
		{
			fieldname: "workflow_state",
			label: __("Workflow State"),
			fieldtype: "Link",
			options: "Workflow State",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "day_status" && data) {
			const colour = {
				Full: "green",
				Partial: "orange",
				"Gate Pass": "blue",
				Incomplete: "red",
				"No Punch": "red",
			}[data.day_status];
			if (colour) value = `<span class="indicator-pill ${colour}">${value}</span>`;
		}
		if (column.fieldname === "is_overdue" && data && data.is_overdue) {
			value = `<span class="text-danger">${__("Overdue")}</span>`;
		}
		return value;
	},
};
