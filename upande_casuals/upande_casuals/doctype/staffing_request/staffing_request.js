// Copyright (c) 2026, ivyprecious and contributors
// For license information, please see license.txt

frappe.ui.form.on("Staffing Request", {
	setup(frm) {
		frm.set_query("task", () => ({
			filters: { is_active: 1, ...(frm.doc.department ? { department: frm.doc.department } : {}) },
		}));
		frm.set_query("casual", "calls", () => ({ filters: { register_status: "Available" } }));
	},

	refresh(frm) {
		if (!frm.is_new() && !["Closed", "Cancelled"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Suggest casuals"), () => frm.trigger("suggest_casuals"));
		}
		if (!frm.is_new() && frm.doc.booked_count) {
			frm.add_custom_button(__("Bookings"), () =>
				frappe.set_route("List", "Casual Booking", { staffing_request: frm.doc.name })
			);
		}
	},

	async suggest_casuals(frm) {
		if (frm.is_dirty()) await frm.save();

		const { message: candidates = [] } = await frappe.call({
			method: "upande_casuals.upande_casuals.doctype.staffing_request.staffing_request.get_candidates",
			args: { staffing_request: frm.doc.name },
		});
		const listed = new Set((frm.doc.calls || []).map((r) => r.casual));
		const fresh = candidates.filter((c) => !listed.has(c.name));
		if (!fresh.length) {
			frappe.msgprint(__("No available casuals match this request."));
			return;
		}

		const fmt = (v, type) => (v ? frappe.format(v, { fieldtype: type }) : "-");
		const rows = fresh
			.map(
				(c) => `<tr>
					<td><input type="checkbox" class="casual-pick" data-name="${frappe.utils.escape_html(c.name)}"></td>
					<td>${frappe.utils.escape_html(c.full_name || c.name)}</td>
					<td>${frappe.utils.escape_html(c.phone || "")}</td>
					<td>${frappe.utils.escape_html(c.designation || "")}</td>
					<td class="text-right">${fmt(c.day_rate, "Currency")}</td>
					<td class="text-right">${c.rating ? c.rating.toFixed(1) : "-"}</td>
					<td class="text-right">${c.days_worked || 0}</td>
					<td>${fmt(c.last_engagement, "Date")}</td>
				</tr>`
			)
			.join("");

		const dialog = new frappe.ui.Dialog({
			title: __("Suggested Casuals"),
			size: "extra-large",
			fields: [{ fieldname: "list", fieldtype: "HTML" }],
			primary_action_label: __("Add to Call Log"),
			primary_action() {
				const picked = dialog.$wrapper
					.find(".casual-pick:checked")
					.map((_, el) => $(el).data("name"))
					.get();
				fresh
					.filter((c) => picked.includes(c.name))
					.forEach((c) => {
						const row = frm.add_child("calls", {
							casual: c.name,
							casual_name: c.full_name,
							phone: c.phone,
						});
						row.called_at = frappe.datetime.now_datetime();
						row.called_by = frappe.session.user;
					});
				frm.refresh_field("calls");
				frm.dirty();
				dialog.hide();
				if (picked.length) {
					frappe.show_alert({
						message: __("Added {0} casual(s). Set each call's outcome, then save.", [picked.length]),
						indicator: "blue",
					});
				}
			},
		});
		dialog.fields_dict.list.$wrapper.html(`
			<table class="table table-bordered table-hover">
				<thead><tr>
					<th><input type="checkbox" class="casual-pick-all"></th>
					<th>${__("Name")}</th><th>${__("Phone")}</th><th>${__("Role")}</th>
					<th class="text-right">${__("Day Rate")}</th><th class="text-right">${__("Rating")}</th>
					<th class="text-right">${__("Days Worked")}</th><th>${__("Last Engaged")}</th>
				</tr></thead>
				<tbody>${rows}</tbody>
			</table>`);
		dialog.$wrapper.on("change", ".casual-pick-all", (e) =>
			dialog.$wrapper.find(".casual-pick").prop("checked", e.target.checked)
		);
		dialog.show();
	},
});
