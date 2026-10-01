// Copyright (c) 2026, ivyprecious and contributors
// For license information, please see license.txt

frappe.listview_settings["Casual Booking"] = {
	add_fields: ["is_overdue", "workflow_state", "docstatus"],
	has_indicator_for_draft: true,
	has_indicator_for_cancelled: true,

	get_indicator(doc) {
		if (doc.is_overdue) {
			return [__("Overdue"), "red", "is_overdue,=,1"];
		}
		if (doc.workflow_state) {
			const colours = {
				Success: "green",
				Warning: "orange",
				Danger: "red",
				Primary: "blue",
				Inverse: "gray",
				Info: "light-blue",
			};
			const style = locals["Workflow State"]?.[doc.workflow_state]?.style;
			return [__(doc.workflow_state), colours[style] || "gray", `workflow_state,=,${doc.workflow_state}`];
		}
		if (doc.docstatus === 2) return [__("Cancelled"), "red", "docstatus,=,2"];
		if (doc.docstatus === 0) return [__("Draft"), "red", "docstatus,=,0"];
	},
};
