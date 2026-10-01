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
	},
});
