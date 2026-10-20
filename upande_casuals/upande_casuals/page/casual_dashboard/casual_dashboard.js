// Copyright (c) 2026, ivyprecious and contributors
// For license information, please see license.txt

const API = "upande_casuals.api";
const REFRESH_MS = 5 * 60 * 1000;
const UNDO_MS = 3000;
const PAGE_LENGTH = 20;

// Built-in palettes. Names must match the Active Theme options in Casual Settings.
const PALETTES = {
	Teal: {
		primary: "#4C7288",
		accent: "#8DADBE",
		background: "#F4F6F8",
		card: "#FFFFFF",
		text: "#2E2E2E",
	},
	"Forest Green": {
		primary: "#2D6A4F",
		accent: "#52B788",
		background: "#F0F7F1",
		card: "#FFFFFF",
		text: "#1B2A1E",
	},
	"Warm Earth": {
		primary: "#8B5E3C",
		accent: "#C9956C",
		background: "#FBF5EE",
		card: "#FFFFFF",
		text: "#2C1A0E",
	},
	"Ocean Blue": {
		primary: "#1A5276",
		accent: "#2E86C1",
		background: "#EAF4FB",
		card: "#FFFFFF",
		text: "#1A2940",
	},
	"Slate Grey": {
		primary: "#4A4A4A",
		accent: "#A0A0A0",
		background: "#F2F2F2",
		card: "#FFFFFF",
		text: "#1A1A1A",
	},
};
const DEFAULT_THEME = "Teal";
const THEME_KEYS = ["primary", "accent", "background", "card", "text"];
// Status colours stay the same across palettes so green always means "good".
const STATUS_COLOURS = { success: "#2E8B57", warning: "#D98E04", danger: "#C0392B" };

const PRESENT = ["Full", "Partial", "Gate Pass", "Incomplete"];

const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
const money = (v) => format_currency(v || 0, "KES", 0);
const fmt_date = (d) => (d ? frappe.datetime.str_to_user(d) : "–");
const fmt_time = (dt) => (dt ? moment(dt).format("HH:mm") : "–");
const fmt_hours = (h) => (h ? flt(h, 2).toFixed(2) : "–");
const initials = (name) =>
	(name || "?")
		.split(/\s+/)
		.slice(0, 2)
		.map((p) => p[0])
		.join("")
		.toUpperCase();

function status_class(status) {
	const s = (status || "").toLowerCase();
	if (/approved|paid|filled|full|gate pass|available/.test(s)) return "good";
	if (/reject|cancel|no punch|do not call/.test(s)) return "bad";
	if (/pending|partial|incomplete|draft|booked|open/.test(s)) return "warn";
	return "info";
}

function chip(status) {
	return `<span class="cd-status ${status_class(status)}">${esc(__(status || "–"))}</span>`;
}

function day_row_class(day_status) {
	if (["Full", "Gate Pass"].includes(day_status)) return "cd-good";
	if (["Partial", "Incomplete"].includes(day_status)) return "cd-warn";
	if (day_status === "No Punch") return "cd-bad";
	return "";
}

// A stable hue per name, so initials circles are easy to tell apart.
function avatar_style(name) {
	let hue = 0;
	for (const ch of name || "") hue = (hue * 31 + ch.charCodeAt(0)) % 360;
	return `background: hsl(${hue} 45% 86%); color: hsl(${hue} 45% 28%)`;
}

function avatar(name, image, large) {
	const cls = `cd-avatar${large ? " cd-avatar-lg" : ""}`;
	return image
		? `<span class="${cls}"><img src="${esc(image)}" alt="" loading="lazy"></span>`
		: `<span class="${cls}" style="${avatar_style(name)}">${esc(initials(name))}</span>`;
}

function empty(message) {
	return `<div class="cd-empty">${esc(message)}</div>`;
}

frappe.pages["casual-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Upande Casuals Management"),
		single_column: true,
	});
	wrapper.casual_dashboard = new CasualDashboard(page);
	// The desk shell is hidden only while this page is showing.
	$(wrapper).on("hide", () => document.body.classList.remove("cd-fullscreen"));
};

frappe.pages["casual-dashboard"].on_page_show = function (wrapper) {
	document.body.classList.add("cd-fullscreen");
	wrapper.casual_dashboard?.refresh_all();
};

class CasualDashboard {
	constructor(page) {
		this.page = page;
		this.$root = $(frappe.render_template("casual_dashboard", {})).appendTo(page.main);
		this.palette = PALETTES[DEFAULT_THEME];
		this.state = {
			report_tab: "attendance",
			filters: { department: null, from_date: null, to_date: null, status: "" },
			start: 0,
			roster: [],
			roster_sort: { field: "full_name", asc: true },
			charts: {},
		};
		this.apply_palette(this.palette);
		this.render_header();
		this.make_approval_filters();
		this.bind_events();
		this.load_theme();
		// on_page_show runs right after this and does the first load.
		this.timer = setInterval(() => {
			if (frappe.get_route_str() === "casual-dashboard" && !document.hidden)
				this.refresh_all();
		}, REFRESH_MS);
	}

	section(name) {
		return this.$root.find(`[data-section="${name}"]`);
	}

	call(method, args = {}, opts = {}) {
		return frappe.xcall(`${API}.${method}`, args, "POST", opts);
	}

	// ------------------------------------------------------------------
	// Events
	// ------------------------------------------------------------------

	bind_events() {
		const $r = this.$root;

		$r.on("click", "[data-action='refresh-all']", () => this.refresh_all());
		$r.on("click", "[data-action='refresh-section']", (e) => {
			const name = $(e.currentTarget).closest("[data-section]").data("section");
			this.refresh_section(name);
		});

		$r.on("click", ".cd-kpi", (e) => this.on_kpi_click($(e.currentTarget)));

		// Approvals
		this.section("approvals").on("click", ".cd-chip", (e) => {
			this.state.filters.status = $(e.currentTarget).data("status") || "";
			this.state.start = 0;
			this.load_approvals();
		});
		this.section("approvals").on("click", "[data-page]", (e) => {
			this.state.start = Math.max(
				0,
				this.state.start + Number($(e.currentTarget).data("page")) * PAGE_LENGTH
			);
			this.load_approvals();
		});
		$r.on("click", "[data-wf-action]", (e) => {
			e.stopPropagation();
			const $b = $(e.currentTarget);
			this.run_workflow_action($b.data("doctype"), $b.data("name"), $b.data("wf-action"));
		});
		this.section("approvals").on("click", "tr[data-request]", (e) =>
			this.show_request(
				this.approval_rows.find((r) => r.name === $(e.currentTarget).data("request"))
			)
		);

		// Attendance
		this.section("attendance").on("click", "tr[data-day]", (e) =>
			this.show_day(this.today_rows.find((r) => r.name === $(e.currentTarget).data("day")))
		);

		// Roster
		const $search = this.section("roster").find(".cd-search");
		$search.on(
			"input",
			frappe.utils.debounce(() => this.render_roster(), 150)
		);
		$search.on("search", () => {
			if (!$search.val()) this.load_roster();
		});
		this.section("roster").on("click", "th[data-sort]", (e) => {
			const field = $(e.currentTarget).data("sort");
			const sort = this.state.roster_sort;
			sort.asc = sort.field === field ? !sort.asc : true;
			sort.field = field;
			this.render_roster();
		});
		this.section("roster").on("click", "[data-roster]", (e) => {
			e.stopPropagation();
			const $b = $(e.currentTarget);
			const casual = this.state.roster.find((c) => c.name === $b.data("name"));
			if ($b.data("roster") === "book") this.book_casual(casual);
			if ($b.data("roster") === "edit")
				window.open(`/app/on-call-casual/${encodeURIComponent(casual.name)}`, "_blank");
			if ($b.data("roster") === "view") this.show_casual(casual.name);
		});
		this.section("roster").on("click", "tr[data-casual]", (e) =>
			this.show_casual($(e.currentTarget).data("casual"))
		);

		// Reports
		this.section("reports").on("click", ".cd-tab", (e) => {
			this.state.report_tab = $(e.currentTarget).data("tab");
			this.make_report_filters();
			this.load_report();
		});
		this.section("reports").on("click", "[data-action='export']", () => this.export_report());
		this.section("reports").on("change", ".cd-pick-all", (e) =>
			this.section("reports").find(".cd-pick").prop("checked", e.target.checked)
		);
		this.section("reports").on("click", "[data-action='approve-selected']", () =>
			this.approve_selected()
		);
		this.section("reports").on("click", "tr[data-day]", (e) =>
			this.show_day(this.report_rows.find((r) => r.name === $(e.currentTarget).data("day")))
		);

		// Quick actions
		const actions = {
			"add-casual": () => this.add_casual(),
			"add-task": () => this.add_task(),
			"add-request": () => this.add_staffing_request(),
			"view-reports": () => this.focus_section("reports", "attendance"),
			"pay-casuals": () => this.pay_casuals(),
		};
		Object.entries(actions).forEach(([action, fn]) =>
			$r.on("click", `[data-action='${action}']`, fn)
		);

		// Drawer and theme panel
		$r.on("click", "[data-action='close-drawer'], .cd-backdrop", () => this.close_drawer());
		$r.on("click", "[data-action='open-theme']", () =>
			this.$root.toggleClass("cd-theme-open")
		);
		$r.on("click", "[data-action='close-theme']", () =>
			this.$root.removeClass("cd-theme-open")
		);
		$r.on("click", ".cd-swatch", (e) => this.choose_theme($(e.currentTarget).data("theme")));
		$(document).on("keydown.casual_dashboard", (e) => {
			if (e.key === "Escape") {
				this.close_drawer();
				this.$root.removeClass("cd-theme-open");
			}
		});
	}

	on_kpi_click($card) {
		let target = $card.data("target");
		let report_tab = $card.data("tab");
		if ($card.data("mine")) {
			// Payments are approved in the Reports panel, staffing requests up top.
			const pending = this.summary?.pending_by_doctype || {};
			if (pending["Casual Booking"] && !pending["Staffing Request"]) {
				target = "reports";
				report_tab = "payments";
			} else {
				this.filter_requests("Mine");
			}
		} else if (target === "approvals") {
			this.filter_requests("Pending");
		}
		this.focus_section(target, target === "reports" ? report_tab : null);
	}

	filter_requests(status) {
		this.state.filters.status = status;
		this.state.start = 0;
		this.load_approvals();
	}

	focus_section(name, report_tab) {
		if (report_tab && name === "reports") {
			this.state.report_tab = report_tab;
			this.make_report_filters();
			this.load_report();
		}
		const $s = this.section(name);
		$s[0].scrollIntoView({ behavior: "smooth", block: "start" });
		$s.removeClass("cd-flash");
		void $s[0].offsetWidth; // restart the animation
		$s.addClass("cd-flash");
	}

	// ------------------------------------------------------------------
	// Refresh
	// ------------------------------------------------------------------

	refresh_all() {
		const $btn = this.$root.find("[data-action='refresh-all']").addClass("cd-spin");
		return Promise.allSettled([
			this.load_summary(),
			this.load_approvals(),
			this.load_attendance(),
			this.load_roster(),
			this.load_report(),
		]).finally(() => {
			$btn.removeClass("cd-spin");
			this.$root.find(".cd-updated").text(__("Updated {0}", [moment().format("HH:mm")]));
		});
	}

	refresh_section(name) {
		const loaders = {
			approvals: () => this.load_approvals(),
			attendance: () => this.load_attendance(),
			roster: () => this.load_roster(),
			reports: () => this.load_report(),
		};
		return loaders[name]?.();
	}

	// After any write: the touched section plus the numbers that depend on it.
	after_change(...sections) {
		this.load_summary();
		sections.forEach((s) => this.refresh_section(s));
	}

	// ------------------------------------------------------------------
	// Header, banner and KPIs
	// ------------------------------------------------------------------

	render_header() {
		this.$root.find(".cd-today").text(moment().format("dddd, D MMMM YYYY"));
		this.$root.find(".cd-user").text(frappe.session.user_fullname || frappe.session.user);
	}

	async load_summary() {
		const s = (this.summary = await this.call("get_summary"));

		const delta = (cur, prev, label) => {
			const diff = flt(cur) - flt(prev);
			if (!diff) return __("Same as {0}", [label]);
			const cls = diff > 0 ? "cd-up" : "cd-down";
			const arrow = diff > 0 ? "▲" : "▼";
			return `<span class="${cls}">${arrow} ${Math.abs(diff).toLocaleString()}</span> ${__(
				"vs {0}",
				[label]
			)}`;
		};
		const pct = (cur, prev) => {
			if (!flt(prev)) return flt(cur) ? __("No pay last week") : "";
			const change = Math.round(((flt(cur) - flt(prev)) / flt(prev)) * 100);
			const cls = change >= 0 ? "cd-up" : "cd-down";
			return `<span class="${cls}">${change >= 0 ? "▲" : "▼"} ${Math.abs(
				change
			)}%</span> ${__("earned vs last week")}`;
		};
		const pending_note = Object.entries(s.pending_by_doctype)
			.filter(([, n]) => n)
			.map(([dt, n]) => `${n} ${__(dt === "Casual Booking" ? "payments" : "requests")}`)
			.join(" · ");

		const cards = {
			active_today: [
				s.active_today,
				delta(s.active_today, s.active_last_week, __("same day last week")),
			],
			open_requests: [
				s.open_requests,
				__("{0} new this week ({1} last week)", [
					s.new_requests_this_week,
					s.new_requests_last_week,
				]),
			],
			pending_approvals: [s.pending_approvals, pending_note || __("Nothing waiting on you")],
			payable_this_week: [
				money(s.payable_this_week),
				pct(s.earned_this_week, s.earned_last_week),
			],
		};
		Object.entries(cards).forEach(([key, [value, note]]) => {
			const $card = this.$root.find(`.cd-kpi[data-kpi="${key}"]`);
			$card.find(".cd-kpi-value").text(value);
			$card.find(".cd-kpi-delta").html(note);
		});

		const summary = [
			__("{0} casuals on duty today", [s.active_today]),
			__("{0} staffing requests open", [s.open_requests]),
			__("{0} payable this week", [money(s.payable_this_week)]),
		].join(" · ");
		this.$root.find(".cd-banner").html(
			`<div class="cd-greeting">${esc(this.greeting(s))}</div>
				<div class="cd-banner-summary">${esc(summary)}</div>`
		);
	}

	// Counts are the user's own approvals, the same numbers as the Pending My Approval card.
	greeting(s) {
		const hour = moment().hour();
		const [salutation, icon] =
			hour < 12
				? [__("Good morning"), "☀️"]
				: hour < 17
				? [__("Good afternoon"), "🌤️"]
				: [__("Good evening"), "🌙"];
		const first_name =
			(frappe.session.user_fullname || "").trim().split(/\s+/)[0] || frappe.session.user;
		const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
		const pending = s.pending_by_doctype || {};
		return __("{0}, {1} {2} — you have {3} pending and {4} awaiting approval.", [
			salutation,
			first_name,
			icon,
			count(
				cint(pending["Staffing Request"]),
				__("staffing request"),
				__("staffing requests")
			),
			count(cint(pending["Casual Booking"]), __("payment"), __("payments")),
		]);
	}

	// ------------------------------------------------------------------
	// Section 2: approvals
	// ------------------------------------------------------------------

	make_approval_filters() {
		const $s = this.section("approvals");
		const f = this.state.filters;
		const make = (fieldname, df) =>
			frappe.ui.form.make_control({
				parent: $s.find(`[data-filter="${fieldname}"]`),
				df: {
					fieldname,
					...df,
					change: () => {
						const value = this.filter_controls[fieldname].get_value() || null;
						if (value === f[fieldname]) return;
						f[fieldname] = value;
						this.state.start = 0;
						this.load_approvals();
					},
				},
				render_input: true,
			});
		this.filter_controls = {
			department: make("department", {
				fieldtype: "Link",
				options: "Department",
				placeholder: __("Department"),
			}),
			from_date: make("from_date", { fieldtype: "Date", placeholder: __("From Date") }),
			to_date: make("to_date", { fieldtype: "Date", placeholder: __("To Date") }),
		};
	}

	async load_approvals() {
		const $s = this.section("approvals");
		$s.find(".cd-chip")
			.removeClass("active")
			.filter((_, el) => ($(el).data("status") || "") === this.state.filters.status)
			.addClass("active");

		const { rows, total } = await this.call("get_staffing_requests", {
			...this.state.filters,
			start: this.state.start,
			page_length: PAGE_LENGTH,
		});
		this.approval_rows = rows;
		$s.find(".cd-body").html(this.requests_table(rows));

		const from = total ? this.state.start + 1 : 0;
		const to = this.state.start + rows.length;
		$s.find(".cd-pager").html(`
			<span>${__("{0}–{1} of {2}", [from, to, total])}</span>
			<span class="cd-row-actions">
				<button class="cd-btn" data-page="-1" ${this.state.start ? "" : "disabled"}>${__(
			"Previous"
		)}</button>
				<button class="cd-btn" data-page="1" ${to < total ? "" : "disabled"}>${__("Next")}</button>
			</span>`);
	}

	action_buttons(doctype, name, actions) {
		return actions
			.map((action) => {
				const cls =
					action === "Approve"
						? "cd-btn-primary"
						: action === "Reject"
						? "cd-btn-danger"
						: "";
				return `<button class="cd-btn ${cls}" data-wf-action="${esc(
					action
				)}" data-doctype="${esc(doctype)}"
					data-name="${esc(name)}">${esc(__(action))}</button>`;
			})
			.join("");
	}

	requests_table(rows) {
		if (!rows.length) return empty(__("No staffing requests match these filters."));
		const body = rows
			.map(
				(r) => `<tr class="cd-clickable" data-request="${esc(r.name)}">
				<td>${fmt_date(r.from_date)}${r.to_date !== r.from_date ? ` – ${fmt_date(r.to_date)}` : ""}</td>
				<td><b>${esc(r.event_name)}</b><div class="text-muted small">${esc(r.name)}</div></td>
				<td>${esc(r.department)}</td>
				<td>${esc(r.requested_by)}</td>
				<td class="cd-num">${r.booked_count || 0} / ${r.headcount}</td>
				<td>${chip(r.workflow_state || r.status)}</td>
				<td><div class="cd-row-actions">${this.action_buttons(
					"Staffing Request",
					r.name,
					r.actions
				)}</div></td>
			</tr>`
			)
			.join("");
		return `<div class="cd-table-wrap"><table class="cd-table">
			<thead><tr>
				<th>${__("Dates")}</th><th>${__("Event")}</th><th>${__("Department")}</th>
				<th>${__("Requested By")}</th><th class="cd-num">${__("Booked / Needed")}</th>
				<th>${__("Status")}</th><th>${__("Actions")}</th>
			</tr></thead><tbody>${body}</tbody></table></div>`;
	}

	run_workflow_action(doctype, name, action) {
		const apply = (reason) =>
			this.call("apply_action", { doctype, name, action, reason }).then((r) => {
				frappe.show_alert({
					message: __("{0}: {1}", [name, __(r.workflow_state || action)]),
					indicator: "green",
				});
				this.after_change("approvals", "reports");
			});

		if (action === "Reject") {
			const d = new frappe.ui.Dialog({
				title: __("Reject {0}", [name]),
				fields: [
					{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
				],
				primary_action_label: __("Reject"),
				primary_action: ({ reason }) => {
					d.hide();
					apply(reason);
				},
			});
			d.show();
			return;
		}

		if (action === "Approve") {
			// Give a short window to undo before anything is sent to the server.
			let undone = false;
			const $alert = frappe.show_alert(
				{
					message: `${esc(
						__("Approving {0}…", [name])
					)} <a class="cd-undo" style="margin-left:8px;font-weight:600">${__(
						"Undo"
					)}</a>`,
					indicator: "blue",
				},
				UNDO_MS / 1000
			);
			$alert?.find(".cd-undo").on("click", () => {
				undone = true;
				$alert.remove();
				frappe.show_alert({
					message: __("Approval of {0} undone", [name]),
					indicator: "orange",
				});
			});
			setTimeout(() => !undone && apply(), UNDO_MS);
			return;
		}

		frappe.confirm(__("{0} {1}?", [__(action), name]), () => apply());
	}

	show_request(r) {
		if (!r) return;
		this.open_drawer(
			r.event_name,
			`<dl class="cd-fields">
				<dt>${__("Request")}</dt><dd><a class="cd-link" href="/app/staffing-request/${encodeURIComponent(
				r.name
			)}" target="_blank">${esc(r.name)}</a></dd>
				<dt>${__("Status")}</dt><dd>${chip(r.workflow_state || r.status)}</dd>
				<dt>${__("Department")}</dt><dd>${esc(r.department)}</dd>
				<dt>${__("Role")}</dt><dd>${esc(r.designation || "–")}</dd>
				<dt>${__("Dates")}</dt><dd>${fmt_date(r.from_date)} – ${fmt_date(r.to_date)}</dd>
				<dt>${__("Booked")}</dt><dd>${r.booked_count || 0} / ${r.headcount}</dd>
				<dt>${__("Requested By")}</dt><dd>${esc(r.requested_by)}</dd>
			</dl>
			<div class="cd-row-actions">${this.action_buttons("Staffing Request", r.name, r.actions)}</div>`
		);
	}

	// ------------------------------------------------------------------
	// Section 3: attendance
	// ------------------------------------------------------------------

	async load_attendance() {
		const $s = this.section("attendance");
		const data = (this.attendance = await this.call("get_attendance_overview"));
		this.today_rows = data.rows;
		this.render_attendance_chart();

		if (!data.rows.length) {
			$s.find(".cd-body").html(empty(__("No casuals are booked for today.")));
			return;
		}
		const body = data.rows
			.map(
				(r) => `<tr class="cd-clickable ${day_row_class(r.day_status)}" data-day="${esc(
					r.name
				)}">
				<td>${esc(r.casual_name)}</td>
				<td>${esc(r.department)}</td>
				<td>${fmt_time(r.first_punch)}</td>
				<td>${fmt_time(r.last_punch)}</td>
				<td class="cd-num">${fmt_hours(r.paid_hours)}</td>
				<td>${chip(r.day_status)}</td>
			</tr>`
			)
			.join("");
		$s.find(".cd-body").html(`<div class="cd-table-wrap"><table class="cd-table">
			<thead><tr>
				<th>${__("Casual")}</th><th>${__("Department")}</th><th>${__("In")}</th><th>${__("Out")}</th>
				<th class="cd-num">${__("Paid Hrs")}</th><th>${__("Status")}</th>
			</tr></thead><tbody>${body}</tbody></table></div>`);
	}

	render_attendance_chart() {
		if (!this.attendance) return;
		const days = this.attendance.days;
		this.make_chart("attendance", this.section("attendance").find(".cd-chart")[0], {
			type: "bar",
			height: 220,
			data: {
				labels: days.map((d) => moment(d.date).format("ddd D")),
				datasets: [
					{ name: __("Present"), values: days.map((d) => d.present) },
					{ name: __("No Punch"), values: days.map((d) => d.absent) },
				],
			},
			colors: [this.palette.primary, this.palette.accent],
			barOptions: { spaceRatio: 0.4 },
			axisOptions: { xIsSeries: true },
		});
	}

	make_chart(key, el, options) {
		$(el).empty();
		this.state.charts[key] = new frappe.Chart(el, options);
	}

	show_day(r) {
		if (!r) return;
		const editable = r.booking_docstatus === 0;
		const overridden = !!(r.override_reason || flt(r.manual_amount));
		this.open_drawer(
			`${r.casual_name} · ${fmt_date(r.work_date)}`,
			`<dl class="cd-fields">
				<dt>${__("Booking")}</dt><dd><a class="cd-link" href="/app/casual-booking/${encodeURIComponent(
				r.booking
			)}" target="_blank">${esc(r.booking)}</a></dd>
				<dt>${__("Department")}</dt><dd>${esc(r.department)}</dd>
				<dt>${__("Status")}</dt><dd>${chip(r.day_status)}</dd>
				<dt>${__("First Punch")}</dt><dd>${fmt_time(r.first_punch)}</dd>
				<dt>${__("Last Punch")}</dt><dd>${fmt_time(r.last_punch)}</dd>
				<dt>${__("Paid Hours")}</dt><dd>${fmt_hours(r.paid_hours)}</dd>
				<dt>${__("Amount")}</dt><dd>${money(r.amount)}</dd>
				${
					overridden
						? `<dt>${__("Override")}</dt><dd>${money(r.manual_amount)} — ${esc(
								r.override_reason
						  )}</dd>`
						: ""
				}
			</dl>
			<div class="cd-drawer-section">
				<div class="cd-subhead">${__("Adjust Pay")}</div>
				${
					editable
						? `<p class="text-muted small">${__(
								"Status is worked out from punches. To pay a different amount for this day, set an override with a reason."
						  )}</p>
						<div class="cd-override-form"></div>
						<div class="cd-row-actions">
							<button class="cd-btn cd-btn-primary" data-override="save">${__("Save Override")}</button>
							${
								overridden
									? `<button class="cd-btn" data-override="clear">${__(
											"Clear Override"
									  )}</button>`
									: ""
							}
						</div>`
						: `<p class="text-muted small">${__(
								"This booking is submitted ({0}), so its days can no longer be changed.",
								[esc(r.workflow_state || "")]
						  )}</p>`
				}
			</div>`
		);
		if (!editable) return;

		const $body = this.$root.find(".cd-drawer-body");
		const $form = $body.find(".cd-override-form");
		const amount = frappe.ui.form.make_control({
			parent: $form,
			df: { fieldname: "manual_amount", fieldtype: "Currency", label: __("Amount (KES)") },
			render_input: true,
		});
		const reason = frappe.ui.form.make_control({
			parent: $form,
			df: { fieldname: "reason", fieldtype: "Data", label: __("Reason") },
			render_input: true,
		});
		amount.set_value(overridden ? r.manual_amount : r.amount);
		reason.set_value(r.override_reason || "");

		$body.find("[data-override]").on("click", (e) => {
			const clear = $(e.currentTarget).data("override") === "clear";
			this.call(
				"override_attendance_day",
				{
					day: r.name,
					manual_amount: amount.get_value(),
					reason: reason.get_value(),
					clear: clear ? 1 : 0,
				},
				{ freeze: true }
			).then(() => {
				frappe.show_alert({ message: __("Saved {0}", [r.booking]), indicator: "green" });
				this.close_drawer();
				this.after_change("attendance", "reports");
			});
		});
	}

	// ------------------------------------------------------------------
	// Section 5: roster
	// ------------------------------------------------------------------

	async load_roster() {
		this.state.roster = await this.call("get_roster");
		this.render_roster();
	}

	render_roster() {
		const $s = this.section("roster");
		const query = ($s.find(".cd-search").val() || "").trim().toLowerCase();
		const digits = query.replace(/\D/g, "");
		const { field, asc } = this.state.roster_sort;

		const rows = this.state.roster
			.filter(
				(c) =>
					!query ||
					(c.full_name || "").toLowerCase().includes(query) ||
					(digits && (c.mpesa_number || "").replace(/\D/g, "").includes(digits))
			)
			.sort((a, b) => {
				const x = a[field] ?? "";
				const y = b[field] ?? "";
				const cmp =
					typeof x === "number" && typeof y === "number"
						? x - y
						: String(x).localeCompare(String(y));
				return asc ? cmp : -cmp;
			});

		if (!rows.length) {
			$s.find(".cd-body").html(
				empty(
					query ? __("No casual matches that search.") : __("No casuals registered yet.")
				)
			);
			return;
		}
		const th = (label, f, cls = "") =>
			`<th class="${cls}" data-sort="${f}">${__(label)}${
				field === f ? (asc ? " ▲" : " ▼") : ""
			}</th>`;
		const body = rows
			.map(
				(c) => `<tr class="cd-clickable" data-casual="${esc(c.name)}">
				<td>${avatar(c.full_name, c.image)}</td>
				<td><b>${esc(c.full_name)}</b><div class="text-muted small">${esc(c.designation || "")}</div></td>
				<td>${esc(c.mpesa_number || c.phone || "–")}</td>
				<td>${esc(c.department)}</td>
				<td class="cd-num">${money(c.day_rate)}</td>
				<td>${fmt_date(c.last_engagement)}</td>
				<td>${chip(c.register_status)}</td>
				<td><div class="cd-row-actions">
					<button class="cd-btn" data-roster="book" data-name="${esc(c.name)}">${__("Book")}</button>
					<button class="cd-btn" data-roster="edit" data-name="${esc(c.name)}">${__("Edit")}</button>
					<button class="cd-btn" data-roster="view" data-name="${esc(c.name)}">${__("View")}</button>
				</div></td>
			</tr>`
			)
			.join("");
		$s.find(".cd-body").html(`<table class="cd-table">
			<thead><tr>
				<th></th>${th("Name", "full_name")}${th("Mpesa", "mpesa_number")}${th("Department", "department")}
				${th("Day Rate", "day_rate", "cd-num")}${th("Last Engaged", "last_engagement")}
				${th("Status", "register_status")}<th></th>
			</tr></thead><tbody>${body}</tbody></table>`);
	}

	async show_casual(name) {
		this.open_drawer(__("Loading…"), "");
		const c = await this.call("get_casual_detail", { name });
		const bookings = c.bookings.length
			? `<table class="cd-table"><thead><tr><th>${__("Dates")}</th><th>${__(
					"Status"
			  )}</th><th class="cd-num">${__("Amount")}</th></tr></thead>
				<tbody>${c.bookings
					.map(
						(
							b
						) => `<tr><td><a class="cd-link" href="/app/casual-booking/${encodeURIComponent(
							b.name
						)}" target="_blank">
							${fmt_date(b.from_date)} – ${fmt_date(b.to_date)}</a></td>
							<td>${chip(b.workflow_state)}</td><td class="cd-num">${money(b.total_amount)}</td></tr>`
					)
					.join("")}</tbody></table>`
			: empty(__("No bookings yet."));
		this.open_drawer(
			c.full_name,
			`<div style="display:flex;gap:14px;align-items:center;margin-bottom:14px">
				${avatar(c.full_name, c.image, true)}
				<div>${chip(c.register_status)}<div class="text-muted small" style="margin-top:4px">${esc(
				c.designation || ""
			)}</div></div>
			</div>
			<dl class="cd-fields">
				<dt>${__("Casual")}</dt><dd><a class="cd-link" href="/app/on-call-casual/${encodeURIComponent(
				c.name
			)}" target="_blank">${esc(c.name)}</a></dd>
				<dt>${__("Mpesa")}</dt><dd>${esc(c.mpesa_number || "–")}</dd>
				<dt>${__("Phone")}</dt><dd>${esc(c.phone || "–")}</dd>
				<dt>${__("National ID")}</dt><dd>${esc(c.national_id || "–")}</dd>
				<dt>${__("Department")}</dt><dd>${esc(c.department)}</dd>
				<dt>${__("Day Rate")}</dt><dd>${money(c.day_rate)}</dd>
				<dt>${__("Rating")}</dt><dd>${c.rating ? `${flt(c.rating, 1)} / 5` : "–"}</dd>
				<dt>${__("Days Worked")}</dt><dd>${c.days_worked || 0}</dd>
			</dl>
			<div class="cd-drawer-section"><div class="cd-subhead">${__(
				"Recent Bookings"
			)}</div>${bookings}</div>`
		);
	}

	book_casual(casual) {
		const d = new frappe.ui.Dialog({
			title: __("Book {0}", [casual.full_name]),
			fields: [
				{
					fieldname: "from_date",
					fieldtype: "Date",
					label: __("From Date"),
					reqd: 1,
					default: frappe.datetime.get_today(),
				},
				{
					fieldname: "to_date",
					fieldtype: "Date",
					label: __("To Date"),
					reqd: 1,
					default: frappe.datetime.get_today(),
				},
				{ fieldname: "col", fieldtype: "Column Break" },
				{
					fieldname: "task",
					fieldtype: "Link",
					options: "Casual Task",
					label: __("Task"),
					get_query: () => ({
						filters: { is_active: 1, department: casual.department },
					}),
				},
				{
					fieldname: "staffing_request",
					fieldtype: "Link",
					options: "Staffing Request",
					label: __("Staffing Request"),
					get_query: () => ({ filters: { status: "Open" } }),
				},
			],
			primary_action_label: __("Create Booking"),
			primary_action: (values) =>
				this.call(
					"create_booking",
					{ payload: { casual: casual.name, ...values } },
					{ freeze: true }
				).then((r) => {
					d.hide();
					this.toast_created(__("Booking"), "casual-booking", r.name);
					this.after_change("attendance", "roster", "approvals");
				}),
		});
		d.show();
	}

	// ------------------------------------------------------------------
	// Section 6: reports
	// ------------------------------------------------------------------

	make_report_filters() {
		const tab = this.state.report_tab;
		const $s = this.section("reports");
		$s.find(".cd-tab").removeClass("active").filter(`[data-tab="${tab}"]`).addClass("active");
		const $f = $s.find(".cd-report-filters").empty();

		const today = frappe.datetime.get_today();
		const month_start = frappe.datetime.month_start();
		const defs = {
			attendance: [
				[
					"from_date",
					{
						fieldtype: "Date",
						label: __("From"),
						default: frappe.datetime.add_days(today, -6),
					},
				],
				["to_date", { fieldtype: "Date", label: __("To"), default: today }],
				[
					"department",
					{ fieldtype: "Link", options: "Department", label: __("Department") },
				],
			],
			pay: [
				["from_date", { fieldtype: "Date", label: __("From"), default: month_start }],
				[
					"to_date",
					{ fieldtype: "Date", label: __("To"), default: frappe.datetime.month_end() },
				],
				["casual", { fieldtype: "Link", options: "On-Call Casual", label: __("Casual") }],
				[
					"department",
					{ fieldtype: "Link", options: "Department", label: __("Department") },
				],
				["task", { fieldtype: "Link", options: "Casual Task", label: __("Task") }],
				[
					"designation",
					{ fieldtype: "Link", options: "Designation", label: __("Designation") },
				],
				[
					"group_by",
					{
						fieldtype: "Select",
						label: __("Chart By"),
						options: "Department\nTask\nDesignation",
						default: "Department",
					},
				],
			],
			payments: [
				[
					"department",
					{ fieldtype: "Link", options: "Department", label: __("Department") },
				],
			],
			engagement: [
				[
					"weeks",
					{
						fieldtype: "Select",
						label: __("Weeks"),
						options: "8\n12\n26\n52",
						default: "12",
					},
				],
			],
		}[tab];

		$s.find("[data-action='export']").toggle(tab !== "payments");
		$s.find("[data-action='approve-selected']").toggle(tab === "payments");

		this.report_controls = {};
		defs.forEach(([fieldname, df]) => {
			const $wrap = $('<div class="cd-filter"></div>').appendTo($f);
			const control = frappe.ui.form.make_control({
				parent: $wrap,
				df: {
					fieldname,
					...df,
					change: frappe.utils.debounce(() => this.load_report(), 300),
				},
				render_input: true,
			});
			control.set_value(df.default || "");
			this.report_controls[fieldname] = control;
		});
	}

	report_args() {
		if (!this.report_controls) this.make_report_filters();
		const args = {};
		Object.entries(this.report_controls).forEach(([k, c]) => {
			const v = c.get_value();
			if (v) args[k] = v;
		});
		return args;
	}

	async load_report() {
		const $s = this.section("reports");
		const tab = this.state.report_tab;
		const args = this.report_args();
		$s.find(".cd-chart").empty();
		const $body = $s.find(".cd-body");

		if (["attendance", "pay"].includes(tab) && !(args.from_date && args.to_date)) {
			$body.html(empty(__("Pick a date range.")));
			return;
		}

		if (tab === "attendance") {
			const rows = (this.report_rows = await this.call("get_casual_attendance", args));
			$body.html(this.attendance_report_table(rows));
		} else if (tab === "pay") {
			const rows = (this.pay_rows = await this.call("get_pay_summary", args));
			this.render_pay_chart();
			$body.html(this.pay_table(rows));
		} else if (tab === "payments") {
			const rows = (this.payment_rows = await this.call("get_pending_bookings", args));
			$body.html(this.payments_table(rows));
		} else {
			const rows = (this.engagement_rows = await this.call("get_engagement_history", args));
			this.render_engagement_chart();
			$body.html(this.engagement_table(rows));
		}
	}

	attendance_report_table(rows) {
		if (!rows.length) return empty(__("No attendance in this period."));
		const body = rows
			.map(
				(r) => `<tr class="cd-clickable ${day_row_class(r.day_status)}" data-day="${esc(
					r.name
				)}">
				<td>${fmt_date(r.work_date)}</td>
				<td>${esc(r.casual_name)}</td>
				<td>${esc(r.department)}</td>
				<td>${fmt_time(r.first_punch)} – ${fmt_time(r.last_punch)}</td>
				<td class="cd-num">${fmt_hours(r.elapsed_hours)}</td>
				<td class="cd-num">${fmt_hours(r.paid_hours)}</td>
				<td>${chip(r.day_status)}</td>
				<td class="cd-num">${money(r.amount)}</td>
			</tr>`
			)
			.join("");
		return `<table class="cd-table"><thead><tr>
			<th>${__("Date")}</th><th>${__("Casual")}</th><th>${__("Department")}</th><th>${__("Punches")}</th>
			<th class="cd-num">${__("Hours Worked")}</th><th class="cd-num">${__("Payable Hours")}</th>
			<th>${__("Status")}</th><th class="cd-num">${__("Amount")}</th>
		</tr></thead><tbody>${body}</tbody></table>`;
	}

	render_pay_chart() {
		const rows = this.pay_rows || [];
		if (!rows.length || this.state.report_tab !== "pay") return;
		const field = (this.report_args().group_by || "Department").toLowerCase();
		const groups = {};
		rows.forEach((r) => {
			const key = r[field] || __("Not Set");
			groups[key] ??= { paid: 0, unpaid: 0 };
			groups[key][r.workflow_state === "Paid" ? "paid" : "unpaid"] += flt(r.gross);
		});
		const labels = Object.keys(groups).sort((a, b) => {
			const total = (k) => groups[k].paid + groups[k].unpaid;
			return total(b) - total(a);
		});
		this.make_chart("pay", this.section("reports").find(".cd-chart")[0], {
			type: "bar",
			height: 240,
			data: {
				labels,
				datasets: [
					{ name: __("Paid"), values: labels.map((k) => groups[k].paid) },
					{ name: __("Unpaid"), values: labels.map((k) => groups[k].unpaid) },
				],
			},
			colors: [this.palette.primary, this.palette.accent],
			barOptions: { stacked: 1, spaceRatio: 0.5 },
			tooltipOptions: { formatTooltipY: (v) => money(v) },
		});
	}

	pay_table(rows) {
		if (!rows.length) return empty(__("No pay matches these filters."));
		const sum = (k) => rows.reduce((t, r) => t + flt(r[k]), 0);
		const body = rows
			.map(
				(r) => `<tr>
				<td><b>${esc(r.casual_name)}</b><div class="text-muted small">${esc(r.booking)}</div></td>
				<td>${esc(r.designation || "–")}</td>
				<td>${esc(r.department || "–")}</td>
				<td>${esc(r.task || "–")}</td>
				<td class="cd-num">${cint(r.days_worked)}</td>
				<td class="cd-num">${fmt_hours(r.total_hours)}</td>
				<td class="cd-num">${money(r.rate)}</td>
				<td class="cd-num">${money(r.gross)}</td>
				<td>${chip(r.payment_status)}<div class="text-muted small">${esc(r.workflow_state)}</div></td>
			</tr>`
			)
			.join("");
		return `<table class="cd-table">
			<thead><tr>
				<th>${__("Casual Name")}</th><th>${__("Designation")}</th><th>${__("Department")}</th>
				<th>${__("Task")}</th><th class="cd-num">${__("Days Worked")}</th>
				<th class="cd-num">${__("Total Hours")}</th><th class="cd-num">${__("Rate")}</th>
				<th class="cd-num">${__("Gross Pay")}</th><th>${__("Payment Status")}</th>
			</tr></thead>
			<tbody>${body}</tbody>
			<tfoot><tr class="cd-total">
				<td>${__("Total")}</td><td></td><td></td><td></td>
				<td class="cd-num">${sum("days_worked")}</td>
				<td class="cd-num">${fmt_hours(sum("total_hours"))}</td><td></td>
				<td class="cd-num">${money(sum("gross"))}</td><td></td>
			</tr></tfoot></table>`;
	}

	payments_table(rows) {
		if (!rows.length) return empty(__("No payments are awaiting approval."));
		const body = rows
			.map((r) => {
				const can_approve = r.actions.includes("Approve");
				return `<tr>
				<td>${can_approve ? `<input type="checkbox" class="cd-pick" data-name="${esc(r.name)}">` : ""}</td>
				<td><b>${esc(r.casual_name)}</b><div class="text-muted small">${esc(r.name)}</div></td>
				<td>${esc(r.department)}</td>
				<td>${fmt_date(r.from_date)} – ${fmt_date(r.to_date)}</td>
				<td class="cd-num">${money(r.total_amount)}</td>
				<td>${esc(r.mpesa_number || "–")}</td>
				<td>${chip(r.workflow_state)}</td>
				<td><div class="cd-row-actions">${this.action_buttons(
					"Casual Booking",
					r.name,
					r.actions
				)}</div></td>
			</tr>`;
			})
			.join("");
		const total = rows.reduce((t, r) => t + flt(r.total_amount), 0);
		return `<table class="cd-table">
			<thead><tr>
				<th><input type="checkbox" class="cd-pick-all" title="${__("Select all")}"></th>
				<th>${__("Casual Name")}</th><th>${__("Department")}</th><th>${__("Period")}</th>
				<th class="cd-num">${__("Gross Pay")}</th><th>${__("Mpesa Number")}</th>
				<th>${__("Status")}</th><th>${__("Actions")}</th>
			</tr></thead>
			<tbody>${body}</tbody>
			<tfoot><tr class="cd-total">
				<td></td><td>${__("Total")}</td><td></td><td></td>
				<td class="cd-num">${money(total)}</td><td></td><td></td><td></td>
			</tr></tfoot></table>`;
	}

	approve_selected() {
		const names = this.section("reports")
			.find(".cd-pick:checked")
			.map((_, el) => $(el).data("name"))
			.get();
		if (!names.length) {
			frappe.show_alert({
				message: __("Select at least one payment."),
				indicator: "orange",
			});
			return;
		}
		const total = (this.payment_rows || [])
			.filter((r) => names.includes(r.name))
			.reduce((t, r) => t + flt(r.total_amount), 0);
		frappe.confirm(
			__("Approve {0} payments totalling {1}?", [names.length, money(total)]),
			async () => {
				const r = await this.call("approve_bookings", { names }, { freeze: true });
				if (r.approved.length) {
					frappe.show_alert(
						{
							message: __("{0} payments approved", [r.approved.length]),
							indicator: "green",
						},
						7
					);
				}
				if (r.failed.length) {
					frappe.msgprint({
						title: __("Some payments were not approved"),
						indicator: "orange",
						message: r.failed
							.map((f) => `<b>${esc(f.name)}</b>: ${esc(f.error)}`)
							.join("<br>"),
					});
				}
				this.after_change("approvals", "reports");
			}
		);
	}

	render_engagement_chart() {
		const rows = this.engagement_rows || [];
		if (!rows.length || this.state.report_tab !== "engagement") return;
		this.make_chart("engagement", this.section("reports").find(".cd-chart")[0], {
			type: "line",
			height: 240,
			data: {
				labels: rows.map((r) => moment(r.week_start).format("D MMM")),
				datasets: [{ name: __("Casual Days"), values: rows.map((r) => r.casual_days) }],
			},
			colors: [this.palette.primary],
			lineOptions: { regionFill: 1, dotSize: 4 },
			axisOptions: { xIsSeries: true },
		});
	}

	engagement_table(rows) {
		const body = rows
			.map(
				(r) =>
					`<tr><td>${__("Week of {0}", [
						fmt_date(r.week_start),
					])}</td><td class="cd-num">${r.casual_days}</td></tr>`
			)
			.join("");
		return `<table class="cd-table"><thead><tr><th>${__("Week")}</th><th class="cd-num">${__(
			"Casual Days"
		)}</th></tr></thead>
			<tbody>${body}</tbody></table>`;
	}

	export_report() {
		const args = { report: this.state.report_tab, ...this.report_args() };
		window.open(`/api/method/${API}.export_report?${$.param(args)}`);
	}

	// ------------------------------------------------------------------
	// Section 4: quick action dialogs
	// ------------------------------------------------------------------

	toast_created(label, route, name) {
		frappe.show_alert(
			{
				message: `${esc(
					__("{0} created:", [label])
				)} <a href="/app/${route}/${encodeURIComponent(name)}" target="_blank">${esc(
					name
				)}</a>`,
				indicator: "green",
			},
			7
		);
	}

	dialog(title, fields, method, on_done, primary_label = __("Create")) {
		const d = new frappe.ui.Dialog({
			title,
			fields,
			size: "large",
			primary_action_label: primary_label,
			primary_action: (values) =>
				this.call(method, { payload: values }, { freeze: true }).then((r) => {
					d.hide();
					on_done(r);
				}),
		});
		d.show();
		return d;
	}

	add_casual() {
		this.dialog(
			__("Add Casual"),
			[
				{ fieldname: "first_name", fieldtype: "Data", label: __("First Name"), reqd: 1 },
				{ fieldname: "middle_name", fieldtype: "Data", label: __("Middle Name") },
				{ fieldname: "last_name", fieldtype: "Data", label: __("Last Name") },
				{
					fieldname: "gender",
					fieldtype: "Link",
					options: "Gender",
					label: __("Gender"),
					reqd: 1,
				},
				{
					fieldname: "date_of_birth",
					fieldtype: "Date",
					label: __("Date of Birth"),
					reqd: 1,
				},
				{ fieldname: "national_id", fieldtype: "Data", label: __("ID Number") },
				{ fieldname: "col1", fieldtype: "Column Break" },
				{
					fieldname: "phone",
					fieldtype: "Data",
					options: "Phone",
					label: __("Phone"),
					reqd: 1,
				},
				{
					fieldname: "mpesa_number",
					fieldtype: "Data",
					options: "Phone",
					label: __("Mpesa Number"),
					description: __("Leave blank if same as phone"),
				},
				{
					fieldname: "department",
					fieldtype: "Link",
					options: "Department",
					label: __("Department"),
					reqd: 1,
				},
				{
					fieldname: "designation",
					fieldtype: "Link",
					options: "Designation",
					label: __("Role"),
				},
				{
					fieldname: "day_rate",
					fieldtype: "Currency",
					label: __("Day Rate"),
					description: __("Blank uses the default rate"),
				},
				{ fieldname: "sec", fieldtype: "Section Break" },
				{
					fieldname: "employee",
					fieldtype: "Link",
					options: "Employee",
					label: __("Existing Employee"),
					description: __(
						"Leave blank to create an Employee automatically (if enabled in Casual Settings)"
					),
				},
				{ fieldname: "col2", fieldtype: "Column Break" },
				{ fieldname: "image", fieldtype: "Attach Image", label: __("Photo") },
			],
			"create_casual",
			(r) => {
				this.toast_created(__("Casual"), "on-call-casual", r.name);
				this.after_change("roster");
			}
		);
	}

	add_task() {
		this.dialog(
			__("Add Task"),
			[
				{ fieldname: "task_name", fieldtype: "Data", label: __("Task Name"), reqd: 1 },
				{
					fieldname: "department",
					fieldtype: "Link",
					options: "Department",
					label: __("Department"),
					reqd: 1,
				},
				{ fieldname: "description", fieldtype: "Small Text", label: __("Description") },
			],
			"create_casual_task",
			(r) => this.toast_created(__("Task"), "casual-task", r.name)
		);
	}

	add_staffing_request() {
		const d = this.dialog(
			__("New Staffing Request"),
			[
				{ fieldname: "event_name", fieldtype: "Data", label: __("Event Name"), reqd: 1 },
				{
					fieldname: "department",
					fieldtype: "Link",
					options: "Department",
					label: __("Department"),
					reqd: 1,
				},
				{
					fieldname: "designation",
					fieldtype: "Link",
					options: "Designation",
					label: __("Role"),
				},
				{
					fieldname: "task",
					fieldtype: "Link",
					options: "Casual Task",
					label: __("Task"),
					get_query: () => ({
						filters: {
							is_active: 1,
							...(d.get_value("department")
								? { department: d.get_value("department") }
								: {}),
						},
					}),
				},
				{ fieldname: "col", fieldtype: "Column Break" },
				{ fieldname: "from_date", fieldtype: "Date", label: __("From Date"), reqd: 1 },
				{ fieldname: "to_date", fieldtype: "Date", label: __("To Date"), reqd: 1 },
				{
					fieldname: "headcount",
					fieldtype: "Int",
					label: __("Number of Casuals"),
					reqd: 1,
					default: 1,
				},
				{ fieldname: "sec", fieldtype: "Section Break" },
				{ fieldname: "notes", fieldtype: "Small Text", label: __("Special Notes") },
			],
			"create_staffing_request",
			(r) => {
				this.toast_created(__("Staffing Request"), "staffing-request", r.name);
				this.after_change("approvals");
			}
		);
	}

	pay_casuals() {
		const week_start = moment().startOf("isoWeek");
		let preview = null;
		const d = new frappe.ui.Dialog({
			title: __("Pay Casuals"),
			size: "extra-large",
			fields: [
				{
					fieldname: "from_date",
					fieldtype: "Date",
					label: __("From Date"),
					reqd: 1,
					default: week_start.clone().subtract(7, "days").format("YYYY-MM-DD"),
					onchange: () => reset(),
				},
				{
					fieldname: "to_date",
					fieldtype: "Date",
					label: __("To Date"),
					reqd: 1,
					default: week_start.clone().subtract(1, "days").format("YYYY-MM-DD"),
					onchange: () => reset(),
				},
				{ fieldname: "col1", fieldtype: "Column Break" },
				{
					fieldname: "casuals",
					fieldtype: "MultiSelectList",
					label: __("Casuals"),
					description: __("Leave empty for all casuals"),
					onchange: () => reset(),
					get_data: (txt) =>
						this.state.roster
							.filter(
								(c) =>
									!txt ||
									(c.full_name || "").toLowerCase().includes(txt.toLowerCase())
							)
							.map((c) => ({ value: c.name, description: c.full_name })),
				},
				{ fieldname: "col2", fieldtype: "Column Break" },
				{
					fieldname: "payment_method",
					fieldtype: "Link",
					options: "Mode of Payment",
					label: __("Payment Method"),
					reqd: 1,
				},
				{
					fieldname: "payment_reference",
					fieldtype: "Data",
					label: __("Payment Reference"),
					description: __("e.g. M-Pesa bulk payment ID"),
				},
				{ fieldname: "sec", fieldtype: "Section Break" },
				{ fieldname: "preview", fieldtype: "HTML" },
			],
			primary_action_label: __("Preview"),
			primary_action: (values) => (preview ? confirm_pay(values) : load_preview(values)),
		});

		const reset = () => {
			preview = null;
			d.fields_dict.preview?.$wrapper.empty();
			d.set_primary_action(__("Preview"), (values) => load_preview(values));
		};

		const load_preview = async (values) => {
			preview = await this.call("preview_casual_pay", {
				from_date: values.from_date,
				to_date: values.to_date,
				casuals: values.casuals?.length ? values.casuals : null,
			});
			const not_ready = preview.not_ready.length
				? `<p class="text-muted small">${__("Not included because not yet approved: {0}", [
						preview.not_ready
							.map((n) => `${n.count} ${esc(n.workflow_state)}`)
							.join(", "),
				  ])}</p>`
				: "";
			if (!preview.rows.length) {
				d.fields_dict.preview.$wrapper.html(
					`<div class="text-muted">${__(
						"No approved, unpaid bookings in this period."
					)}</div>${not_ready}`
				);
				preview = null;
				return;
			}
			const body = preview.rows
				.map(
					(r) => `<tr><td>${esc(r.casual_name)}</td><td>${esc(
						r.mpesa_number || "–"
					)}</td><td>${esc(r.name)}</td>
					<td>${fmt_date(r.from_date)} – ${fmt_date(r.to_date)}</td><td class="text-right">${
						r.days_paid || 0
					}</td>
					<td class="text-right">${fmt_hours(r.total_hours)}</td><td class="text-right">${money(
						r.day_rate
					)}</td>
					<td class="text-right">${money(r.total_amount)}</td></tr>`
				)
				.join("");
			d.fields_dict.preview.$wrapper.html(`
				<table class="table table-bordered table-condensed">
					<thead><tr><th>${__("Casual")}</th><th>${__("Mpesa")}</th><th>${__("Booking")}</th><th>${__(
				"Dates"
			)}</th>
						<th class="text-right">${__("Days")}</th><th class="text-right">${__("Hours")}</th>
						<th class="text-right">${__("Day Rate")}</th><th class="text-right">${__(
				"Gross"
			)}</th></tr></thead>
					<tbody>${body}</tbody>
					<tfoot><tr><th colspan="7">${__("Total")}</th><th class="text-right">${money(
				preview.total
			)}</th></tr></tfoot>
				</table>${not_ready}`);
			d.set_primary_action(__("Mark {0} Bookings Paid", [preview.rows.length]), (v) =>
				confirm_pay(v)
			);
		};

		const confirm_pay = (values) => {
			frappe.confirm(
				__("Mark {0} bookings as paid, totalling {1}?", [
					preview.rows.length,
					money(preview.total),
				]),
				async () => {
					const r = await this.call(
						"pay_casuals",
						{
							bookings: preview.rows.map((row) => row.name),
							payment_method: values.payment_method,
							payment_reference: values.payment_reference,
						},
						{ freeze: true, freeze_message: __("Marking bookings paid…") }
					);
					d.hide();
					if (r.paid.length) {
						frappe.show_alert(
							{
								message: __("{0} bookings marked paid", [r.paid.length]),
								indicator: "green",
							},
							7
						);
					}
					if (r.failed.length) {
						frappe.msgprint({
							title: __("Some bookings were not paid"),
							indicator: "orange",
							message: r.failed
								.map((f) => `<b>${esc(f.name)}</b>: ${esc(f.error)}`)
								.join("<br>"),
						});
					}
					this.after_change("approvals", "reports");
				}
			);
		};

		d.show();
	}

	// ------------------------------------------------------------------
	// Drawer
	// ------------------------------------------------------------------

	open_drawer(title, html) {
		this.$root.find(".cd-drawer-title").text(title || "");
		this.$root.find(".cd-drawer-body").html(html);
		this.$root.addClass("cd-drawer-open");
	}

	close_drawer() {
		this.$root.removeClass("cd-drawer-open");
	}

	// ------------------------------------------------------------------
	// Theme
	// ------------------------------------------------------------------

	async load_theme() {
		this.theme = await this.call("get_dashboard_settings");
		this.apply_theme(this.theme.active_theme);
		this.render_theme_panel();
		this.render_brand();
	}

	render_brand() {
		const { logo, company } = this.theme;
		const $logo = this.$root.find(".cd-logo").toggleClass("cd-logo-image", !!logo);
		if (logo) {
			$logo.html(`<img src="${esc(logo)}" alt="">`);
		} else {
			// Neutral placeholder: initials of the configured company, else a generic icon.
			$logo.html(
				company
					? esc(initials(company))
					: '<svg class="es-icon icon-sm"><use href="#es-line-people"></use></svg>'
			);
		}
		this.$root.find(".cd-subtitle").text(company || "");
	}

	palette_for(name) {
		if (name === "Custom") {
			const custom = this.theme?.custom || {};
			const base = PALETTES[DEFAULT_THEME];
			return Object.fromEntries(THEME_KEYS.map((k) => [k, custom[k] || base[k]]));
		}
		return PALETTES[name] || PALETTES[DEFAULT_THEME];
	}

	apply_theme(name) {
		this.apply_palette(this.palette_for(name));
		this.$root
			.find(".cd-swatch")
			.removeClass("active")
			.filter(`[data-theme="${name}"]`)
			.addClass("active");
	}

	apply_palette(palette) {
		this.palette = palette;
		// Tokens go on the dashboard root, not :root, so the desk UI is never restyled.
		const style = this.$root[0].style;
		style.setProperty("--dash-primary", palette.primary);
		style.setProperty("--dash-accent", palette.accent);
		style.setProperty("--dash-bg", palette.background);
		style.setProperty("--dash-card", palette.card);
		style.setProperty("--dash-text", palette.text);
		style.setProperty("--dash-success", STATUS_COLOURS.success);
		style.setProperty("--dash-warning", STATUS_COLOURS.warning);
		style.setProperty("--dash-danger", STATUS_COLOURS.danger);
		// Charts take colours as values, so redraw them in the new palette.
		this.render_attendance_chart();
		this.render_pay_chart();
		this.render_engagement_chart();
	}

	render_theme_panel() {
		const can_edit = this.theme.can_edit;
		const swatches = [...Object.keys(PALETTES), "Custom"]
			.map((name) => {
				const p = this.palette_for(name);
				return `<button class="cd-swatch ${
					name === this.theme.active_theme ? "active" : ""
				}" data-theme="${esc(name)}"
					${can_edit ? "" : "disabled"}>
					<span class="cd-swatch-bar">${THEME_KEYS.map(
						(k) => `<span style="background:${esc(p[k])}"></span>`
					).join("")}</span>
					<span>${esc(__(name))}</span>
				</button>`;
			})
			.join("");
		this.$root.find(".cd-swatches").html(swatches);
		this.$root
			.find(".cd-theme-note")
			.text(
				can_edit
					? __("The theme is shared by everyone who uses this dashboard.")
					: __("Only HR Managers and System Managers can change the theme.")
			);
		this.render_custom_colours();
	}

	render_custom_colours() {
		const $c = this.$root.find(".cd-custom-colours").empty();
		if (!this.theme.can_edit || this.theme.active_theme !== "Custom") return;
		const p = this.palette_for("Custom");
		THEME_KEYS.forEach((k) => {
			$c.append(`<label for="cd-colour-${k}">${__(frappe.utils.to_title_case(k))}</label>
				<input type="color" id="cd-colour-${k}" data-key="${k}" value="${esc(p[k])}">`);
		});
		$c.append(
			`<span></span><button class="cd-btn cd-btn-primary" data-action="save-custom">${__(
				"Save"
			)}</button>`
		);
		$c.find("input[type=color]").on("input", () =>
			this.apply_palette(this.read_custom_colours())
		);
		$c.find("[data-action='save-custom']").on("click", () =>
			this.save_theme("Custom", this.read_custom_colours())
		);
	}

	read_custom_colours() {
		const values = {};
		this.$root
			.find(".cd-custom-colours input[type=color]")
			.each((_, el) => (values[$(el).data("key")] = el.value));
		return values;
	}

	choose_theme(name) {
		if (!this.theme?.can_edit || name === this.theme.active_theme) return;
		if (name === "Custom") {
			// Preview from stored custom colours; saved when the user presses Save.
			this.theme.active_theme = "Custom";
			this.apply_theme("Custom");
			this.render_custom_colours();
			return;
		}
		this.save_theme(name);
	}

	async save_theme(name, custom) {
		const previous = this.theme.active_theme;
		this.apply_theme(name);
		try {
			this.theme = await this.call("set_dashboard_theme", { theme: name, custom });
			this.render_theme_panel();
			frappe.show_alert({ message: __("Theme set to {0}", [__(name)]), indicator: "green" });
		} catch (e) {
			this.theme.active_theme = previous;
			this.apply_theme(previous);
		}
	}
}
