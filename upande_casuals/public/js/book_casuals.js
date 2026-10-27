// Copyright (c) 2026, ivyprecious and contributors
// For license information, please see license.txt

// Book Casuals dialog, shared by the Staffing Request form and the Casual Dashboard.
frappe.provide("upande_casuals");

upande_casuals.book_casuals = async function (staffing_request, on_done) {
	const method = (name) =>
		`upande_casuals.upande_casuals.doctype.staffing_request.staffing_request.${name}`;
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const fmt = (v, fieldtype) => (v ? frappe.format(v, { fieldtype }) : "–");

	const ctx = await frappe.xcall(method("get_booking_context"), { staffing_request });
	const req = ctx.request;
	if (!ctx.can_book) {
		frappe.msgprint(__("Casuals can only be booked on an approved staffing request."));
		return;
	}
	const remaining = Math.max(0, req.headcount - ctx.booked_count);

	const d = new frappe.ui.Dialog({
		title: __("Book Casuals: {0}", [req.event_name || req.name]),
		size: "extra-large",
		fields: [
			{ fieldname: "details", fieldtype: "HTML" },
			{ fieldname: "search", fieldtype: "Data", placeholder: __("Search name, phone or role") },
			{ fieldname: "list", fieldtype: "HTML" },
		],
		primary_action_label: __("Book Selected"),
		primary_action: async () => {
			const picked = d.$wrapper
				.find(".bc-pick:checked")
				.map((_, el) => $(el).data("name"))
				.get();
			if (!picked.length) {
				frappe.show_alert({ message: __("Select at least one casual."), indicator: "orange" });
				return;
			}
			const r = await frappe.xcall(
				method("book_casuals_for_request"),
				{ staffing_request, casual_names: picked },
				"POST",
				{ freeze: true, freeze_message: __("Booking casuals…") }
			);
			d.hide();
			if (r.count) {
				frappe.show_alert(
					{
						message: __("{0} casual(s) booked. {1} of {2} now booked.", [
							r.count,
							r.booked_count,
							req.headcount,
						]),
						indicator: "green",
					},
					7
				);
			}
			if (r.skipped.length) {
				const names = Object.fromEntries(ctx.candidates.map((c) => [c.name, c.full_name]));
				frappe.msgprint({
					title: __("Some casuals were not booked"),
					indicator: "orange",
					message: r.skipped
						.map((s) => `<b>${esc(names[s.casual] || s.casual)}</b>: ${esc(s.reason)}`)
						.join("<br>"),
				});
			}
			on_done?.(r);
		},
	});

	d.fields_dict.details.$wrapper.html(`
		<dl class="row small" style="margin-bottom: 0">
			<dt class="col-sm-3">${__("Event / Job")}</dt><dd class="col-sm-9">${esc(req.event_name)}</dd>
			<dt class="col-sm-3">${__("Department")}</dt><dd class="col-sm-9">${esc(req.department)}</dd>
			<dt class="col-sm-3">${__("Dates")}</dt>
			<dd class="col-sm-9">${fmt(req.from_date, "Date")} – ${fmt(req.to_date, "Date")}</dd>
			<dt class="col-sm-3">${__("Booked / Requested")}</dt>
			<dd class="col-sm-9">${ctx.booked_count} / ${req.headcount}
				<span class="text-muted bc-count"></span></dd>
		</dl>`);

	const rows = ctx.candidates
		.map(
			(c) => `<tr data-search="${esc(
				[c.full_name, c.name, c.phone, c.designation].join(" ").toLowerCase()
			)}">
				<td><input type="checkbox" class="bc-pick" data-name="${esc(c.name)}"></td>
				<td>${esc(c.full_name || c.name)}</td>
				<td>${esc(c.phone || "")}</td>
				<td>${esc(c.designation || "")}</td>
				<td class="text-right">${fmt(c.day_rate, "Currency")}</td>
				<td class="text-right">${c.rating ? flt(c.rating).toFixed(1) : "–"}</td>
				<td>${fmt(c.last_engagement, "Date")}</td>
			</tr>`
		)
		.join("");
	d.fields_dict.list.$wrapper.html(
		ctx.candidates.length
			? `<div style="max-height: 50vh; overflow-y: auto">
				<table class="table table-bordered table-hover table-condensed">
					<thead><tr>
						<th></th><th>${__("Name")}</th><th>${__("Phone")}</th><th>${__("Role")}</th>
						<th class="text-right">${__("Day Rate")}</th><th class="text-right">${__("Rating")}</th>
						<th>${__("Last Engaged")}</th>
					</tr></thead>
					<tbody>${rows}</tbody>
				</table></div>`
			: `<div class="text-muted">${__(
					"No available casuals in this department are free on these dates."
			  )}</div>`
	);

	// Never let more be picked than the request still needs.
	const update_count = () => {
		const n = d.$wrapper.find(".bc-pick:checked").length;
		d.$wrapper.find(".bc-count").text(__("({0} selected, {1} still needed)", [n, remaining]));
		d.$wrapper.find(".bc-pick:not(:checked)").prop("disabled", n >= remaining);
	};
	d.$wrapper.on("change", ".bc-pick", update_count);
	d.fields_dict.search.$input.on("input", (e) => {
		const q = (e.target.value || "").trim().toLowerCase();
		d.$wrapper
			.find("tr[data-search]")
			.each((_, tr) => $(tr).toggle(!q || tr.dataset.search.includes(q)));
	});
	update_count();
	d.show();
};
