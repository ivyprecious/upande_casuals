// Copyright (c) 2026, ivyprecious and contributors
// For license information, please see license.txt

frappe.ui.form.on("Casual Settings", {
	seed_missing_defaults(frm) {
		frappe.confirm(
			__("Fill empty settings and create any missing records? Existing values are kept."),
			() =>
				frappe
					.call(
						"upande_casuals.upande_casuals.doctype.casual_settings.casual_settings.seed_missing_defaults"
					)
					.then(() => frm.reload_doc())
		);
	},
});
