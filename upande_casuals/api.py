# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

"""Whitelisted methods behind the Casual Dashboard page.

Attendance lives in Casual Booking Day rows and pay on Casual Booking, so there is
no separate attendance or pay slip doctype. Approvals always go through
`apply_workflow`, which enforces the role, condition and self-approval rules of
whatever workflow the site has configured.

Workflows must not be managed in code. They are configured on each site through
the UI, so this app ships no Workflow or Workflow Transition fixtures and never
creates or edits them. Anything workflow-aware reads the live workflow at runtime
(`get_workflow`, `get_transitions`) instead of assuming state or action names.
"""

from collections import Counter, defaultdict

import frappe
from frappe import _
from frappe.desk.utils import provide_binary_file
from frappe.model.workflow import (
	WorkflowStateError,
	apply_workflow,
	get_transitions,
	has_approval_access,
)
from frappe.utils import add_days, cint, flt, get_datetime, getdate
from frappe.utils.csvutils import build_csv_response
from frappe.utils.pdf import get_pdf
from frappe.utils.xlsxutils import build_xlsx_response

from upande_casuals.pay_engine import PRO_RATA, compute_day, day_window, get_gate_pass
from upande_casuals.tasks import business_date, get_settings
from upande_casuals.upande_casuals.doctype.casual_booking.casual_booking import RETURNABLE_ROLES
from upande_casuals.upande_casuals.doctype.staffing_request.staffing_request import can_book_casuals
from upande_casuals.workflow import (
	approval_states,
	approve_transitions,
	ended_states,
	get_live_workflow,
	is_danger,
	paid_states,
	pay_transitions,
	payable_states,
	rejected_states,
	settled_states,
	sql_not_in,
)

SETTINGS = "Casual Settings"
APPROVAL_DOCTYPES = ("Staffing Request", "Casual Booking")
PRESENT_STATUSES = ("Full", "Partial", "Gate Pass", "Incomplete")
THEMES = ("Teal", "Forest Green", "Warm Earth", "Ocean Blue", "Slate Grey", "Custom")
THEME_FIELDS = ("theme_primary", "theme_accent", "theme_background", "theme_card", "theme_text")
MAX_RANGE_DAYS = 366
MONTHLY_ROLES = ("HR Manager", "HR User", "HR Officer", "Group HR Manager", "System Manager")
# On TSH, monthly casuals and on-call casuals share this Employment Type. What sets
# them apart is moved_to_monthly and register_status on the On-Call Casual, and
# custom_is_on_call on the Employee.
MONTHLY_EMPLOYMENT_TYPE = "Casual"

# Same rule as pay_engine.effective_amount, for use inside SQL.
EFFECTIVE_AMOUNT = """(case when d.manual_amount != 0 or ifnull(d.override_reason, '') != ''
	then d.manual_amount else d.amount end)"""
DAY_JOIN = "`tabCasual Booking Day` d join `tabCasual Booking` b on b.name = d.parent and d.parenttype = 'Casual Booking'"


def _live_booking():
	"""SQL: a booking counts toward attendance and pay unless it was cancelled, or
	is in a state the live workflow rejects into."""
	rejected = rejected_states(get_live_workflow("Casual Booking"))
	return f"b.docstatus < 2 and {sql_not_in('b.workflow_state', rejected)}"


def _json(value):
	return frappe.parse_json(value) if isinstance(value, str) else (value or {})


def _week_start(date):
	date = getdate(date)
	return add_days(date, -date.weekday())


def _date_range(from_date, to_date):
	from_date, to_date = getdate(from_date), getdate(to_date)
	if to_date < from_date:
		frappe.throw(_("To Date cannot be before From Date."))
	if (to_date - from_date).days > MAX_RANGE_DAYS:
		frappe.throw(_("Pick a range of at most {0} days.").format(MAX_RANGE_DAYS))
	return from_date, to_date


def _pick(payload, fields):
	return {f: payload.get(f) for f in fields if payload.get(f) not in (None, "")}


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_dashboard_settings():
	settings = get_settings()
	return {
		"active_theme": settings.get("active_theme") or THEMES[0],
		"custom": {f.removeprefix("theme_"): settings.get(f) for f in THEME_FIELDS},
		"logo": settings.get("dashboard_logo"),
		"company": settings.company or frappe.defaults.get_global_default("company"),
		"can_edit": frappe.has_permission(SETTINGS, "write"),
	}


@frappe.whitelist()
def set_dashboard_theme(theme, custom=None):
	if theme not in THEMES:
		frappe.throw(_("Unknown theme {0}.").format(theme))
	settings = frappe.get_doc(SETTINGS)
	settings.active_theme = theme
	if theme == "Custom":
		custom = _json(custom)
		for field in THEME_FIELDS:
			key = field.removeprefix("theme_")
			if custom.get(key):
				settings.set(field, custom[key])
	settings.save()  # checks write permission: HR Manager and System Manager only
	return get_dashboard_settings()


# ---------------------------------------------------------------------------
# Workflow helpers
# ---------------------------------------------------------------------------


def _workflow(doctype):
	return get_live_workflow(doctype)


def _action_kind(workflow, transition):
	"""How the dashboard asks before applying a transition, read from its target state:
	a state coloured Danger in Workflow State needs a reason, a cancelled state a
	confirmation, and anything else gets a short undo window."""
	if is_danger(transition.next_state):
		return "reason"
	doc_status = next((s.doc_status for s in workflow.states if s.state == transition.next_state), 0)
	return "confirm" if cint(doc_status) == 2 else "forward"


def _actions(doc, workflow):
	"""Workflow actions the current user can take on doc, in workflow order, as
	{"action", "kind"} (see _action_kind)."""
	if not workflow:
		return []
	try:
		transitions = get_transitions(doc, workflow, raise_exception=True)
	except WorkflowStateError:
		return []
	actions = {}
	for t in transitions:
		if t.action not in actions and has_approval_access(frappe.session.user, doc, t):
			actions[t.action] = _action_kind(workflow, t)
	return [{"action": action, "kind": kind} for action, kind in actions.items()]


def _approve_actions(doc, workflow, actions=None):
	"""The approve actions (see workflow.py) the current user can take on doc now."""
	if not workflow:
		return []
	state = doc.get(workflow.workflow_state_field)
	approve = {t.action for t in approve_transitions(workflow) if t.state == state}
	if actions is None:
		actions = _actions(doc, workflow)
	return [a["action"] for a in actions if a["action"] in approve]


def _single_approve_action(doc, workflow, actions=None):
	"""The approve action for bulk approval, or None when there is none or a choice."""
	approve = _approve_actions(doc, workflow, actions)
	return approve[0] if len(approve) == 1 else None


def _pending_for_user(doctype, limit=200):
	workflow = _workflow(doctype)
	if not workflow:
		return []
	roles = set(frappe.get_roles())
	states = {t.state for t in approve_transitions(workflow) if t.allowed in roles}
	if not states:
		return []
	names = frappe.get_list(
		doctype, filters={workflow.workflow_state_field: ["in", list(states)]}, pluck="name", limit=limit
	)
	return [n for n in names if _approve_actions(frappe.get_doc(doctype, n), workflow)]


@frappe.whitelist()
def apply_action(doctype, name, action, reason=None):
	if doctype not in APPROVAL_DOCTYPES:
		frappe.throw(_("Actions are not available for {0} here.").format(doctype))

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	kind = next((a["kind"] for a in _actions(doc, _workflow(doctype)) if a["action"] == action), None)
	if kind == "reason" and not (reason or "").strip():
		frappe.throw(_("Give a reason to {0}.").format(_(action).lower()))
	# apply_workflow re-checks the role, condition and self-approval rules.
	doc = apply_workflow(doc.as_dict(), action)
	if reason:
		doc.add_comment("Comment", _("{0}: {1}").format(_(action), reason.strip()))
	return {"name": doc.name, "workflow_state": doc.get("workflow_state"), "status": doc.get("status")}


# ---------------------------------------------------------------------------
# Live attendance
# ---------------------------------------------------------------------------


def _day_rows(work_date):
	return frappe.db.sql(
		f"""select d.name, d.work_date, d.day_status, d.first_punch, d.last_punch, d.paid_hours,
			d.manual_amount, d.override_reason, {EFFECTIVE_AMOUNT} as amount,
			b.name as booking, b.casual, b.casual_name, b.employee, b.department, b.day_rate,
			b.docstatus as booking_docstatus
		from {DAY_JOIN}
		where {_live_booking()} and d.work_date = %s
		order by b.casual_name""",
		work_date,
		as_dict=True,
	)


def _live_attendance(work_date, settings):
	"""Day rows for work_date with status recomputed from punches right now.

	Stored rows are only refreshed by the hourly job, so today's view reads
	Employee Checkin directly with the same rules as pay_engine.
	"""
	rows = _day_rows(work_date)
	if settings.punch_source == "Manual":
		return rows

	start, end = day_window(work_date, cint(settings.day_cutoff_hour))
	employees = [r.employee for r in rows if r.employee]
	punches = defaultdict(list)
	if employees:
		for c in frappe.get_all(
			"Employee Checkin",
			filters={"employee": ["in", employees], "time": ["between", [start, end]]},
			fields=["employee", "time"],
		):
			punches[c.employee].append(get_datetime(c.time))

	for row in rows:
		if row.override_reason or flt(row.manual_amount):
			continue  # HR has fixed this day by hand
		result = compute_day(
			punches.get(row.employee, []),
			flt(row.day_rate),
			flt(settings.full_day_hours),
			flt(settings.break_hours),
			bool(get_gate_pass(row.employee, work_date, settings)),
			short_day_policy=settings.short_day_policy or PRO_RATA,
			rounding=bool(cint(settings.round_to_shilling)),
		)
		row.update(
			{k: result[k] for k in ("first_punch", "last_punch", "paid_hours", "day_status", "amount")}
		)
	return rows


def _present_count(work_date):
	return frappe.db.sql(
		f"""select count(distinct b.casual) from {DAY_JOIN}
		where {_live_booking()} and d.work_date = %s and d.day_status in %s""",
		(work_date, PRESENT_STATUSES),
	)[0][0]


@frappe.whitelist()
def get_attendance_overview():
	settings = get_settings()
	today = business_date(settings)
	today_rows = _live_attendance(today, settings)

	start = add_days(today, -6)
	counts = frappe.db.sql(
		f"""select d.work_date, sum(d.day_status in %(present)s) as present,
			sum(d.day_status = 'No Punch') as absent
		from {DAY_JOIN}
		where {_live_booking()} and d.work_date between %(start)s and %(end)s
		group by d.work_date""",
		{"present": PRESENT_STATUSES, "start": start, "end": add_days(today, -1)},
		as_dict=True,
	)
	by_date = {getdate(c.work_date): c for c in counts}
	days = []
	for i in range(7):
		date = add_days(start, i)
		if date == today:
			present = sum(1 for r in today_rows if r.day_status in PRESENT_STATUSES)
			absent = sum(1 for r in today_rows if r.day_status == "No Punch")
		else:
			c = by_date.get(date) or {}
			present, absent = cint(c.get("present")), cint(c.get("absent"))
		days.append({"date": date, "present": present, "absent": absent})

	return {"today": today, "rows": today_rows, "days": days}


# ---------------------------------------------------------------------------
# KPIs and tables
# ---------------------------------------------------------------------------


def _amount_between(start, end, unpaid_only=False):
	paid = paid_states(_workflow("Casual Booking"))
	paid_filter = f"and {sql_not_in('b.workflow_state', paid)}" if unpaid_only else ""
	return flt(
		frappe.db.sql(
			f"""select sum({EFFECTIVE_AMOUNT}) from {DAY_JOIN}
			where {_live_booking()} {paid_filter} and d.work_date between %s and %s""",
			(start, end),
		)[0][0]
	)


@frappe.whitelist()
def get_summary():
	settings = get_settings()
	today = business_date(settings)
	week_start = _week_start(today)
	last_week_start = add_days(week_start, -7)

	on_duty = sum(1 for r in _live_attendance(today, settings) if r.day_status in PRESENT_STATUSES)
	pending = {dt: _pending_for_user(dt) for dt in APPROVAL_DOCTYPES}

	return {
		"today": today,
		"week_start": week_start,
		"active_today": on_duty,
		"active_last_week": _present_count(add_days(today, -7)),
		"open_requests": frappe.db.count("Staffing Request", {"status": "Open"}),
		"new_requests_this_week": frappe.db.count("Staffing Request", {"creation": [">=", week_start]}),
		"new_requests_last_week": frappe.db.count(
			"Staffing Request", {"creation": ["between", [last_week_start, add_days(week_start, -1)]]}
		),
		"pending_approvals": sum(len(v) for v in pending.values()),
		"pending_by_doctype": {dt: len(v) for dt, v in pending.items()},
		"payable_this_week": _amount_between(week_start, add_days(week_start, 6), unpaid_only=True),
		"earned_this_week": _amount_between(week_start, add_days(week_start, 6)),
		"earned_last_week": _amount_between(last_week_start, add_days(week_start, -1)),
	}


def _state_groups(workflow):
	"""The live states behind each dashboard status chip, in workflow order.

	Approved: approved states and what follows them. Rejected: states approval
	rejects into, plus cancelled or Danger states. Pending: everything else.
	"""
	if not workflow:
		return {}
	settled = settled_states(workflow)
	closed = (rejected_states(workflow) | ended_states(workflow)) - settled
	states = [s.state for s in workflow.states]
	return {
		"Pending": [s for s in states if s not in settled and s not in closed],
		"Approved": [s for s in states if s in settled],
		"Rejected": [s for s in states if s in closed],
	}


def _state_filter(meta, workflow, group):
	"""Translate a dashboard status chip (Pending / Approved / Rejected) to filters."""
	if not group:
		return {}
	if workflow and meta.has_field(workflow.workflow_state_field):
		return {workflow.workflow_state_field: ["in", _state_groups(workflow).get(group) or [""]]}
	# No workflow: the request's own Status field.
	return {
		"status": {"Pending": "Open", "Approved": ["in", ["Filled", "Closed"]], "Rejected": "Cancelled"}[
			group
		]
	}


@frappe.whitelist()
def get_staffing_requests(
	department=None, from_date=None, to_date=None, status=None, start=0, page_length=20
):
	doctype = "Staffing Request"
	meta = frappe.get_meta(doctype)
	workflow = _workflow(doctype)
	if status == "Mine":
		filters = {"name": ["in", _pending_for_user(doctype) or [""]]}
	else:
		filters = _state_filter(meta, workflow, status)
	if department:
		filters["department"] = department
	if from_date:
		filters["to_date"] = [">=", from_date]
	if to_date:
		filters["from_date"] = ["<=", to_date]

	fields = [
		"name",
		"event_name",
		"department",
		"designation",
		"task",
		"from_date",
		"to_date",
		"headcount",
		"booked_count",
		"status",
		"notes",
		"owner",
	]
	if meta.has_field("workflow_state"):
		fields.append("workflow_state")

	rows = frappe.get_list(
		doctype,
		filters=filters,
		fields=fields,
		order_by="from_date desc, creation desc",
		start=cint(start),
		page_length=cint(page_length) or 20,
	)
	for row in rows:
		doc = frappe.get_doc(doctype, row.name)
		row.requested_by = frappe.utils.get_fullname(row.owner)
		row.actions = _actions(doc, workflow)
		row.can_book = can_book_casuals(doc, workflow)
		row.fully_booked = cint(row.booked_count) >= cint(row.headcount)
	return {
		"rows": rows,
		"total": len(frappe.get_list(doctype, filters=filters, pluck="name")),
		"groups": _state_groups(workflow) if meta.has_field("workflow_state") else {},
	}


@frappe.whitelist()
def get_pending_bookings(department=None):
	"""Bookings waiting for pay approval. A booking is the pay record: approving it
	submits the booking and makes it payable."""
	workflow = _workflow("Casual Booking")
	states = approval_states(workflow)
	if not states:
		return []
	filters = {"workflow_state": ["in", list(states)]}
	if department:
		filters["department"] = department
	rows = frappe.get_list(
		"Casual Booking",
		filters=filters,
		fields=[
			"name",
			"casual",
			"casual_name",
			"mpesa_number",
			"department",
			"from_date",
			"to_date",
			"days_paid",
			"total_amount",
			"workflow_state",
			"staffing_request",
		],
		order_by="from_date desc",
		limit=100,
	)
	for row in rows:
		doc = frappe.get_doc("Casual Booking", row.name)
		row.actions = _actions(doc, workflow)
		row.can_approve = bool(_single_approve_action(doc, workflow, row.actions))
	return rows


@frappe.whitelist()
def approve_bookings(names):
	"""Approve several bookings, each on its own so one failure does not undo the rest."""
	names = _json(names)
	if not names:
		frappe.throw(_("No bookings selected."))
	workflow = _workflow("Casual Booking")
	approved, failed = [], []
	for name in names:
		frappe.db.savepoint("casual_approve")
		try:
			action = _single_approve_action(frappe.get_doc("Casual Booking", name), workflow)
			if not action:
				frappe.throw(_("{0} has no approve action for you to take.").format(name))
			apply_action("Casual Booking", name, action)
			approved.append(name)
		except Exception as e:
			frappe.db.rollback(save_point="casual_approve")
			frappe.clear_last_message()
			failed.append({"name": name, "error": frappe.utils.strip_html(str(e))})
	return {"approved": approved, "failed": failed}


def _can_edit(doc, workflow):
	"""Whether the form would let the user edit doc in its current workflow state."""
	if doc.docstatus != 0 or not doc.has_permission("write"):
		return False
	if not workflow:
		return True
	state = next((s for s in workflow.states if s.state == doc.get(workflow.workflow_state_field)), None)
	return not state or state.allow_edit in frappe.get_roles()


def _returnables(doc):
	"""Returnable items as the form shows them, including items added to Casual
	Settings after the booking was saved. Nothing is written here."""
	doc.sync_returnable_items()
	return [
		{
			"item_name": r.item_name,
			"returned": cint(r.returned),
			"returned_by": r.returned_by,
			"returned_on": r.returned_on,
		}
		for r in doc.returnable_items
	]


@frappe.whitelist()
def get_booking_approvals(department=None, from_date=None, to_date=None, status="Mine"):
	"""Casual Bookings for the approvals panel.

	Mine: bookings the user can take a workflow action on, or whose returned items
	they still need to tick. Pending: every booking in a state that some role can
	Approve from. Both are read from the live workflow.
	"""
	workflow = _workflow("Casual Booking")
	if not workflow:
		return []
	roles = set(frappe.get_roles())
	can_tick = bool(roles & set(RETURNABLE_ROLES))

	awaiting = approval_states(workflow)
	if status == "Pending":
		states = set(awaiting)
	else:
		states = {t.state for t in workflow.transitions if t.state in awaiting and t.allowed in roles}
		if can_tick:
			states |= {s.state for s in workflow.states if cint(s.doc_status) < 2}
	if not states:
		return []

	filters = {workflow.workflow_state_field: ["in", list(states)], "docstatus": ["<", 2]}
	if department:
		filters["department"] = department
	if from_date:
		filters["to_date"] = [">=", from_date]
	if to_date:
		filters["from_date"] = ["<=", to_date]

	rows = []
	for name in frappe.get_list(
		"Casual Booking", filters=filters, pluck="name", order_by="from_date desc", limit=200
	):
		doc = frappe.get_doc("Casual Booking", name)
		actions = _actions(doc, workflow)
		returnables = _returnables(doc)
		tickable = can_tick and not doc.returnables_locked()
		pending_returns = any(not r["returned"] for r in returnables)
		awaiting_me = doc.workflow_state in awaiting and actions
		if status != "Pending" and not (awaiting_me or (tickable and pending_returns)):
			continue
		rows.append(
			{
				**{
					f: doc.get(f)
					for f in (
						"name",
						"casual",
						"casual_name",
						"mpesa_number",
						"department",
						"from_date",
						"to_date",
						"payment_method",
						"total_amount",
						"staffing_request",
						"workflow_state",
					)
				},
				"actions": actions,
				"returnables": returnables,
				"can_tick": tickable,
				"can_edit": _can_edit(doc, workflow),
			}
		)
	return rows


@frappe.whitelist()
def set_booking_payment_method(name, payment_method):
	"""Set the payment method from the dashboard. Approving submits the booking,
	which needs a payment method, so approvers can fill it in without the form."""
	doc = frappe.get_doc("Casual Booking", name)
	if not _can_edit(doc, _workflow("Casual Booking")):
		frappe.throw(_("You cannot edit {0} in its current state.").format(name), frappe.PermissionError)
	if not frappe.db.exists("Mode of Payment", payment_method):
		frappe.throw(_("Mode of Payment {0} does not exist.").format(payment_method))
	doc.payment_method = payment_method
	doc.save()
	return {"name": doc.name, "payment_method": doc.payment_method}


# ---------------------------------------------------------------------------
# Roster and record panels
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_roster():
	casuals = frappe.get_list(
		"On-Call Casual",
		fields=[
			"name",
			"full_name",
			"employee",
			"mpesa_number",
			"phone",
			"department",
			"designation",
			"day_rate",
			"rating",
			"days_worked",
			"last_engagement",
			"register_status",
			"image",
		],
		order_by="full_name asc",
		limit=0,
	)
	# Casuals without their own photo fall back to the linked Employee's.
	employees = [c.employee for c in casuals if c.employee and not c.image]
	images = (
		dict(
			frappe.get_all(
				"Employee", filters={"name": ["in", employees]}, fields=["name", "image"], as_list=True
			)
		)
		if employees
		else {}
	)
	for c in casuals:
		c.image = c.image or images.get(c.employee)
	return casuals


@frappe.whitelist()
def get_casual_detail(name):
	doc = frappe.get_doc("On-Call Casual", name)
	doc.check_permission("read")
	bookings = frappe.get_list(
		"Casual Booking",
		filters={"casual": name},
		fields=["name", "from_date", "to_date", "workflow_state", "days_paid", "total_amount", "department"],
		order_by="from_date desc",
		limit=10,
	)
	data = doc.as_dict()
	if not data.image and doc.employee:
		data.image = frappe.db.get_value("Employee", doc.employee, "image")
	data.bookings = bookings
	return data


@frappe.whitelist()
def override_attendance_day(day, manual_amount=None, reason=None, clear=0):
	"""Set or clear HR's manual amount on one booking day (the only way to change a
	day's pay by hand; day status itself is always computed from punches)."""
	from upande_casuals.pay_engine import compute_booking

	parent = frappe.db.get_value("Casual Booking Day", day, "parent")
	if not parent:
		frappe.throw(_("Attendance day {0} not found.").format(day))
	doc = frappe.get_doc("Casual Booking", parent)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(
			_("Only days on draft bookings can be changed. {0} is already submitted.").format(doc.name)
		)

	row = next(r for r in doc.days if r.name == day)
	if cint(clear):
		row.manual_amount, row.override_reason = 0, None
		compute_booking(doc)
	else:
		if not (reason or "").strip():
			frappe.throw(_("Give a reason for the override."))
		row.manual_amount, row.override_reason = flt(manual_amount), reason.strip()
	doc.save()
	return next(r for r in doc.days if r.name == day).as_dict()


# ---------------------------------------------------------------------------
# Quick actions
# ---------------------------------------------------------------------------


@frappe.whitelist()
def create_casual(payload):
	payload = _json(payload)
	payload.setdefault("mpesa_number", payload.get("phone"))
	doc = frappe.get_doc(
		{
			"doctype": "On-Call Casual",
			**_pick(
				payload,
				(
					"employee",
					"first_name",
					"middle_name",
					"last_name",
					"gender",
					"date_of_birth",
					"national_id",
					"phone",
					"mpesa_number",
					"department",
					"designation",
					"day_rate",
					"notes",
					"image",
				),
			),
		}
	).insert()
	return {"name": doc.name, "full_name": doc.full_name, "employee": doc.employee}


# ---------------------------------------------------------------------------
# Move to monthly casual
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_monthly_eligible_casuals():
	frappe.only_for(MONTHLY_ROLES)
	return frappe.get_all(
		"On-Call Casual",
		filters={"monthly_casual_eligible": 1, "moved_to_monthly": 0},
		fields=[
			"name",
			"full_name",
			"employee",
			"department",
			"designation",
			"days_worked",
			"last_engagement",
		],
		order_by="last_engagement desc",
	)


@frappe.whitelist()
def move_to_monthly_casual(casual_name, date_of_joining, department, designation):
	"""Put an on-call casual on monthly payroll by updating the Employee it is named
	after. The Employee keeps its existing ID, whatever its naming series."""
	frappe.only_for(MONTHLY_ROLES)
	casual = frappe.get_doc("On-Call Casual", casual_name)
	if cint(casual.moved_to_monthly):
		frappe.throw(_("{0} has already been moved to monthly payroll.").format(casual.full_name))
	if not casual.employee:
		frappe.throw(_("{0} has no linked Employee.").format(casual.full_name))

	employee = frappe.get_doc("Employee", casual.employee)
	employee.update(
		{
			"date_of_joining": getdate(date_of_joining),
			"department": department,
			"designation": designation,
			"employment_type": MONTHLY_EMPLOYMENT_TYPE,
			"status": "Active",
			# Monthly casuals are no longer on call, so they come back into appraisals.
			"custom_is_on_call": 0,
		}
	)
	employee.save(ignore_permissions=True)

	# Set directly: both flags are system-owned and validate() keeps them that way.
	frappe.db.set_value(
		"On-Call Casual", casual.name, {"moved_to_monthly": 1, "register_status": "Do Not Call"}
	)
	casual.add_comment("Info", _("Moved to monthly payroll as Employee {0}.").format(employee.name))
	return {"employee": employee.name, "employee_name": employee.employee_name}


@frappe.whitelist()
def create_casual_task(payload):
	payload = _json(payload)
	doc = frappe.get_doc(
		{
			"doctype": "Casual Task",
			"is_active": 1,
			**_pick(payload, ("task_name", "department", "description")),
		}
	).insert()
	return {"name": doc.name}


@frappe.whitelist()
def create_staffing_request(payload):
	payload = _json(payload)
	doc = frappe.get_doc(
		{
			"doctype": "Staffing Request",
			**_pick(
				payload,
				(
					"event_name",
					"department",
					"designation",
					"task",
					"from_date",
					"to_date",
					"headcount",
					"notes",
				),
			),
		}
	).insert()
	return {"name": doc.name, "workflow_state": doc.get("workflow_state")}


@frappe.whitelist()
def create_booking(payload):
	payload = _json(payload)
	doc = frappe.get_doc(
		{
			"doctype": "Casual Booking",
			**_pick(payload, ("casual", "from_date", "to_date", "task", "staffing_request", "day_rate")),
		}
	).insert()
	return {"name": doc.name, "casual_name": doc.casual_name}


# ---------------------------------------------------------------------------
# Pay Casuals
# ---------------------------------------------------------------------------


def _payable_bookings(from_date, to_date, casuals=None, workflow=None):
	"""Bookings in a state the live workflow can pay from."""
	states = payable_states(workflow)
	if not states:
		return []
	filters = {
		"workflow_state": ["in", list(states)],
		"from_date": ["<=", to_date],
		"to_date": [">=", from_date],
	}
	if casuals:
		filters["casual"] = ["in", casuals]
	return frappe.get_list(
		"Casual Booking",
		filters=filters,
		fields=[
			"name",
			"casual",
			"casual_name",
			"mpesa_number",
			"department",
			"from_date",
			"to_date",
			"day_rate",
			"days_paid",
			"total_hours",
			"total_amount",
		],
		order_by="casual_name asc, from_date asc",
		limit=0,
	)


def _pay_action(doc, workflow, payment_method=None):
	"""The workflow action the current user may take now to pay doc, or None.

	Read from the live workflow with the same role, condition and self-approval
	checks as apply_workflow. Conditions are evaluated with the payment method
	about to be used, since paying sets it.
	"""
	pay = {(t.state, t.action, t.next_state) for t in pay_transitions(workflow)}
	if payment_method:
		doc.payment_method = payment_method
	try:
		transitions = get_transitions(doc, workflow, raise_exception=True)
	except WorkflowStateError:
		return None
	return next(
		(
			t.action
			for t in transitions
			if (t.state, t.action, t.next_state) in pay and has_approval_access(frappe.session.user, doc, t)
		),
		None,
	)


@frappe.whitelist()
def preview_casual_pay(from_date, to_date, casuals=None, payment_method=None):
	from_date, to_date = _date_range(from_date, to_date)
	casuals = _json(casuals) or None
	workflow = _workflow("Casual Booking")
	rows = _payable_bookings(from_date, to_date, casuals, workflow)
	for row in rows:
		doc = frappe.get_doc("Casual Booking", row.name)
		# Same check as the booking's own pay step: every returnable item must be ticked.
		doc.sync_returnable_items()
		row.pending_returns = doc.pending_returns()
		row.can_pay = bool(_pay_action(doc, workflow, payment_method))
	ready = [r for r in rows if r.can_pay and not r.pending_returns]

	# Draft bookings in the period: not approved yet, so HR knows what is missing.
	not_ready_filters = {
		"docstatus": 0,
		"from_date": ["<=", to_date],
		"to_date": [">=", from_date],
	}
	if casuals:
		not_ready_filters["casual"] = ["in", casuals]
	not_ready = Counter(
		frappe.get_list("Casual Booking", filters=not_ready_filters, pluck="workflow_state", limit=0)
	)
	return {
		"rows": rows,
		"ready": [r.name for r in ready],
		"total": sum(flt(r.total_amount) for r in ready),
		"not_ready": [{"workflow_state": s or _("Draft"), "count": n} for s, n in not_ready.items()],
	}


@frappe.whitelist()
def pay_casuals(bookings, payment_method, payment_reference=None):
	"""Pay the previewed bookings with the pay action the live workflow allows the
	user, one at a time so that one failure does not undo the others."""
	bookings = _json(bookings)
	if not bookings:
		frappe.throw(_("No bookings selected."))
	if not frappe.db.exists("Mode of Payment", payment_method):
		frappe.throw(_("Mode of Payment {0} does not exist.").format(payment_method))

	workflow = _workflow("Casual Booking")
	paid, failed = [], []
	for name in bookings:
		frappe.db.savepoint("casual_pay")
		try:
			doc = frappe.get_doc("Casual Booking", name)
			doc.check_permission("read")
			action = _pay_action(doc, workflow, payment_method)
			if not action:
				frappe.throw(
					_("You cannot pay {0} while it is {1}.").format(name, _(doc.workflow_state or "Draft"))
				)
			# apply_workflow reloads from the database, so the payment method is stored first.
			frappe.db.set_value(
				"Casual Booking",
				name,
				{"payment_method": payment_method, "payment_reference": payment_reference},
				update_modified=False,
			)
			apply_workflow(doc.as_dict(), action)
			paid.append(name)
		except Exception as e:
			frappe.db.rollback(save_point="casual_pay")
			frappe.clear_last_message()
			failed.append({"name": name, "error": frappe.utils.strip_html(str(e))})
	return {"paid": paid, "failed": failed}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _check_report_access():
	if not frappe.has_permission("Casual Booking", "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


@frappe.whitelist()
def get_casual_attendance(from_date, to_date, department=None):
	_check_report_access()
	from_date, to_date = _date_range(from_date, to_date)
	dept = "and b.department = %(department)s" if department else ""
	return frappe.db.sql(
		f"""select d.name, d.work_date, b.casual_name, b.casual, b.department, b.name as booking,
			b.workflow_state, b.docstatus as booking_docstatus, d.first_punch, d.last_punch,
			d.elapsed_hours, d.paid_hours, d.day_status, d.gate_pass, d.manual_amount,
			d.override_reason, {EFFECTIVE_AMOUNT} as amount
		from {DAY_JOIN}
		where {_live_booking()} and d.work_date between %(from_date)s and %(to_date)s {dept}
		order by d.work_date desc, b.casual_name""",
		{"from_date": from_date, "to_date": to_date, "department": department},
		as_dict=True,
	)


PAY_FILTERS = ("casual", "department", "task", "designation")


@frappe.whitelist()
def get_pay_summary(from_date, to_date, casual=None, department=None, task=None, designation=None):
	"""One row per booking, counting only its days inside the date range."""
	_check_report_access()
	from_date, to_date = _date_range(from_date, to_date)
	values = {"from_date": from_date, "to_date": to_date}
	conditions = []
	for field, value in zip(PAY_FILTERS, (casual, department, task, designation), strict=True):
		if value:
			conditions.append(f"b.{field} = %({field})s")
			values[field] = value
	extra = "".join(f" and {c}" for c in conditions)

	rows = frappe.db.sql(
		f"""select b.name as booking, b.casual, b.casual_name, b.designation, b.department, b.task,
			b.day_rate as rate, b.workflow_state, b.mpesa_number,
			sum({EFFECTIVE_AMOUNT} > 0) as days_worked,
			sum(d.paid_hours) as total_hours,
			sum({EFFECTIVE_AMOUNT}) as gross
		from {DAY_JOIN}
		where {_live_booking()} and d.work_date between %(from_date)s and %(to_date)s{extra}
		group by b.name
		order by b.casual_name, b.from_date""",
		values,
		as_dict=True,
	)
	paid = paid_states(_workflow("Casual Booking"))
	for r in rows:
		r.is_paid = r.workflow_state in paid
		r.payment_status = _("Paid") if r.is_paid else _("Unpaid")
	return rows


@frappe.whitelist()
def get_engagement_history(weeks=12):
	_check_report_access()
	weeks = min(max(cint(weeks), 1), 52)
	first = add_days(_week_start(business_date()), -7 * (weeks - 1))
	rows = frappe.db.sql(
		f"""select d.work_date, count(*) as casual_days
		from {DAY_JOIN}
		where {_live_booking()} and d.work_date >= %s and {EFFECTIVE_AMOUNT} > 0
		group by d.work_date""",
		first,
		as_dict=True,
	)
	totals = defaultdict(int)
	for r in rows:
		totals[_week_start(r.work_date)] += cint(r.casual_days)
	return [
		{"week_start": add_days(first, 7 * i), "casual_days": totals.get(add_days(first, 7 * i), 0)}
		for i in range(weeks)
	]


REPORT_TITLES = {
	"attendance": "Attendance Report",
	"pay": "Pay Summary",
	"engagement": "Engagement History",
	"payments": "Payments Awaiting Approval",
}
EXPORT_FORMATS = ("CSV", "Excel", "PDF")


def _report_data(
	report, from_date=None, to_date=None, department=None, weeks=12, casual=None, task=None, designation=None
):
	"""Header row plus data rows for a dashboard report, as shown with its filters."""
	if report == "attendance":
		data = [
			[
				"Date",
				"Casual",
				"Department",
				"Booking",
				"First Punch",
				"Last Punch",
				"Elapsed Hours",
				"Paid Hours",
				"Status",
				"Amount",
				"Override Reason",
			]
		]
		for r in get_casual_attendance(from_date, to_date, department):
			data.append(
				[
					r.work_date,
					r.casual_name,
					r.department,
					r.booking,
					r.first_punch,
					r.last_punch,
					flt(r.elapsed_hours, 2),
					flt(r.paid_hours, 2),
					r.day_status,
					flt(r.amount),
					r.override_reason,
				]
			)
	elif report == "pay":
		data = [
			[
				"Casual Name",
				"Designation",
				"Department",
				"Task",
				"Booking",
				"Days Worked",
				"Total Hours",
				"Rate (KES)",
				"Gross Pay (KES)",
				"Payment Status",
				"Booking Status",
			]
		]
		rows = get_pay_summary(from_date, to_date, casual, department, task, designation)
		for r in rows:
			data.append(
				[
					r.casual_name,
					r.designation,
					r.department,
					r.task,
					r.booking,
					cint(r.days_worked),
					flt(r.total_hours, 2),
					flt(r.rate),
					flt(r.gross),
					r.payment_status,
					r.workflow_state,
				]
			)
		data.append(
			[
				"Total",
				"",
				"",
				"",
				"",
				sum(cint(r.days_worked) for r in rows),
				flt(sum(flt(r.total_hours) for r in rows), 2),
				"",
				sum(flt(r.gross) for r in rows),
				"",
				"",
			]
		)
	elif report == "payments":
		data = [
			[
				"Booking",
				"Casual Name",
				"Department",
				"From",
				"To",
				"Gross Pay (KES)",
				"Mpesa Number",
				"Status",
			]
		]
		rows = get_pending_bookings(department)
		for r in rows:
			data.append(
				[
					r.name,
					r.casual_name,
					r.department,
					r.from_date,
					r.to_date,
					flt(r.total_amount),
					r.mpesa_number,
					r.workflow_state,
				]
			)
		data.append(["Total", "", "", "", "", sum(flt(r.total_amount) for r in rows), "", ""])
	elif report == "engagement":
		data = [["Week Starting", "Casual Days"]]
		data += [[r["week_start"], r["casual_days"]] for r in get_engagement_history(weeks)]
	else:
		frappe.throw(_("Unknown report {0}.").format(report))

	return data


@frappe.whitelist()
def export_report(
	report,
	file_format="CSV",
	from_date=None,
	to_date=None,
	department=None,
	weeks=12,
	casual=None,
	task=None,
	designation=None,
):
	if report not in REPORT_TITLES:
		frappe.throw(_("Unknown report {0}.").format(report))
	if file_format not in EXPORT_FORMATS:
		frappe.throw(_("Unknown export format {0}.").format(file_format))

	data = _report_data(report, from_date, to_date, department, weeks, casual, task, designation)
	dated = report in ("attendance", "pay")
	filename = f"casual-{report}-{from_date}-{to_date}" if dated else f"casual-{report}"
	if file_format == "CSV":
		build_csv_response(data, filename)
	elif file_format == "Excel":
		build_xlsx_response(data, filename)
	else:
		html = frappe.render_template(
			"upande_casuals/templates/report_export.html",
			{
				"title": _(REPORT_TITLES[report]),
				"subtitle": _report_subtitle(report, from_date, to_date, weeks, department),
				"header": data[0],
				"rows": [[_pdf_cell(v) for v in row] for row in data[1:]],
				"generated_on": frappe.utils.format_datetime(frappe.utils.now_datetime()),
				"company": frappe.db.get_single_value(SETTINGS, "company"),
			},
		)
		provide_binary_file(filename, "pdf", get_pdf(html, {"orientation": "Landscape"}))


def _pdf_cell(value):
	from datetime import date, datetime

	if isinstance(value, bool) or value is None:
		return {"value": value or "", "num": False}
	if isinstance(value, int | float):
		fieldtype = "Int" if isinstance(value, int) else "Float"
		return {"value": frappe.format(value, {"fieldtype": fieldtype, "precision": 2}), "num": True}
	if isinstance(value, datetime):
		return {"value": frappe.format(value, "Datetime"), "num": False}
	if isinstance(value, date):
		return {"value": frappe.format(value, "Date"), "num": False}
	return {"value": value, "num": False}


def _report_subtitle(report, from_date, to_date, weeks, department):
	if report == "engagement":
		period = _("Last {0} weeks").format(cint(weeks))
	elif report == "payments":
		period = _("All bookings awaiting approval")
	else:
		period = _("{0} to {1}").format(frappe.format(from_date, "Date"), frappe.format(to_date, "Date"))
	return f"{period} · {department}" if department else period
