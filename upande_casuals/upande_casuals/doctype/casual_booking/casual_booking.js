// Copyright (c) 2026, ivyprecious and contributors
// For license information, please see license.txt

frappe.ui.form.on("Casual Booking", {
	setup(frm) {
		frm.set_query("casual", () => ({ filters: { register_status: "Available" } }));
		frm.set_query("task", () => ({
			filters: { is_active: 1, ...(frm.doc.department ? { department: frm.doc.department } : {}) },
		}));
	},

	onload(frm) {
		frappe.db.get_single_value("Casual Settings", "punch_source").then((source) => {
			frm.__manual_punches = source === "Manual";
			frm.trigger("set_punch_fields");
		});
	},

	refresh(frm) {
		frm.trigger("set_punch_fields");
		if (frm.doc.is_overdue) {
			frm.dashboard.set_headline_alert(
				__("Payment is overdue. It was due {0}.", [frappe.datetime.str_to_user(frm.doc.pay_due_at)]),
				"red"
			);
		}
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Recalculate"), () =>
				frappe
					.call({
						method: "upande_casuals.upande_casuals.doctype.casual_booking.casual_booking.recalculate",
						args: { name: frm.doc.name },
						freeze: true,
						freeze_message: __("Reading punches..."),
					})
					.then(() => frm.reload_doc())
			);
		}
	},

	set_punch_fields(frm) {
		// Punches are typed in only when Casual Settings uses the Manual punch source.
		const editable = frm.__manual_punches && frm.doc.docstatus === 0;
		const grid = frm.fields_dict.days.grid;
		["first_punch", "last_punch"].forEach((f) =>
			grid.update_docfield_property(f, "read_only", editable ? 0 : 1)
		);
	},
});
