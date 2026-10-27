// Copyright (c) 2026, ivyprecious and contributors
// For license information, please see license.txt

frappe.ui.form.on("On-Call Casual", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Bookings"), () =>
			frappe.set_route("List", "Casual Booking", { casual: frm.doc.name })
		);
		frm.add_custom_button(__("Engagement Register"), () =>
			frappe.set_route("query-report", "Casual Engagement Register", { casual: frm.doc.name })
		);
		if (upande_casuals.can_move_to_monthly() && !frm.doc.moved_to_monthly) {
			frm.add_custom_button(__("Move to Monthly Casual"), () =>
				upande_casuals.move_to_monthly(frm.doc, () => frm.reload_doc())
			);
		}
		// System-set flag: HR Manager and System Manager may clear it, never set it.
		frm.set_df_property(
			"monthly_casual_eligible",
			"read_only",
			!(
				frm.doc.monthly_casual_eligible &&
				(frappe.user.has_role("HR Manager") || frappe.user.has_role("System Manager"))
			)
		);
	},
});
