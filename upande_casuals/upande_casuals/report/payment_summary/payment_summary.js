// Copyright (c) 2026, ivyprecious and contributors
// For license information, please see license.txt

frappe.query_reports["Payment Summary"] = {
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
	],

	formatter(value, row, column, data, default_formatter) {
		// The totals row has no data object, so it gets no button.
		if (column.fieldname === "preview") {
			if (!data || !data.preview) return "";
			return `<button class="btn btn-xs btn-default" data-payment-preview="${frappe.utils.escape_html(
				data.preview
			)}">${__("Preview")}</button>`;
		}
		return default_formatter(value, row, column, data);
	},

	onload(report) {
		report.page.wrapper.on("click", "[data-payment-preview]", (e) => {
			e.stopPropagation();
			show_payment_breakdown(
				$(e.currentTarget).data("payment-preview"),
				report.get_filter_value("from_date"),
				report.get_filter_value("to_date")
			);
		});
	},
};

function show_payment_breakdown(casual, from_date, to_date) {
	frappe
		.xcall(
			"upande_casuals.upande_casuals.report.payment_summary.payment_summary.get_breakdown",
			{ casual, from_date, to_date }
		)
		.then((r) => {
			const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
			const money = (v) => format_currency(v || 0);
			let running = 0;
			const body = r.days
				.map((d) => {
					running += flt(d.amount);
					return `<tr>
						<td>${frappe.datetime.str_to_user(d.work_date)}</td>
						<td class="text-right">${flt(d.paid_hours, 2).toFixed(2)}</td>
						<td class="text-right">${money(d.rate)}</td>
						<td class="text-right">${money(d.amount)}</td>
						<td class="text-right">${money(running)}</td>
						<td>${esc(__(d.day_status || ""))}</td>
						<td><a href="/app/casual-booking/${encodeURIComponent(d.booking)}" target="_blank">${esc(
							d.booking
						)}</a><div class="text-muted small">${esc(__(d.workflow_state || ""))}</div></td>
					</tr>`;
				})
				.join("");
			const requests = r.staffing_requests.length
				? r.staffing_requests
						.map(
							(s) =>
								`<a href="/app/staffing-request/${encodeURIComponent(
									s.name
								)}" target="_blank">${esc(s.name)}</a>${
									s.event_name ? ` (${esc(s.event_name)})` : ""
								}`
						)
						.join(", ")
				: `<span class="text-muted">${__("None")}</span>`;

			const d = new frappe.ui.Dialog({
				title: __("{0}: {1} to {2}", [
					r.casual_name || casual,
					frappe.datetime.str_to_user(from_date),
					frappe.datetime.str_to_user(to_date),
				]),
				size: "extra-large",
				fields: [{ fieldname: "breakdown", fieldtype: "HTML" }],
			});
			d.fields_dict.breakdown.$wrapper.html(`
				<p><b>${__("Staffing Requests")}:</b> ${requests}</p>
				<table class="table table-bordered table-condensed">
					<thead><tr>
						<th>${__("Date")}</th>
						<th class="text-right">${__("Hours Worked")}</th>
						<th class="text-right">${__("Rate")}</th>
						<th class="text-right">${__("Amount")}</th>
						<th class="text-right">${__("Running Total")}</th>
						<th>${__("Day Status")}</th>
						<th>${__("Booking")}</th>
					</tr></thead>
					<tbody>${body}</tbody>
					<tfoot><tr>
						<th colspan="4">${__("Total")}</th>
						<th class="text-right">${money(running)}</th>
						<th colspan="2"></th>
					</tr></tfoot>
				</table>`);
			d.show();
		});
}
