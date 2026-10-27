# Copyright (c) 2026, ivyprecious and contributors
# For license information, please see license.txt

"""Read what each state of a live workflow means, without knowing any names.

Workflows are configured on each site through the UI (see api.py), so state and
action names can change at any time. Each state's role is worked out from the
workflow's shape instead:

- approval state: a draft (doc status 0) state where a decision is made, i.e.
  one with two or more different actions, such as approve and reject.
- approve transition: from an approval state to a later approval state, or to
  an approved state.
- approved state: where approval ends. Reached from an approval state, not one
  itself, with no way back into approval, and not cancelled or coloured Danger.
- rejected state: reached from an approval state by any other transition.
- ended state: cancelled (doc status 2) or coloured Danger in Workflow State.
- paid state (Casual Booking): reached by a move between two submitted states.
  Approving submits a booking and cancelling ends it, so paying is the only such
  move. Its source states are payable.

Every helper takes the workflow (None when the doctype has none) and returns an
empty result rather than raising, so callers simply show raw state names.
"""

from collections import defaultdict

import frappe
from frappe.model.workflow import get_workflow, get_workflow_name
from frappe.utils import cint

DANGER = "Danger"


def get_live_workflow(doctype):
	return get_workflow(doctype) if get_workflow_name(doctype) else None


def _doc_status(workflow):
	return {s.state: cint(s.doc_status) for s in workflow.states}


def _reachable(workflow, start):
	"""States reachable from start, including start."""
	seen, todo = set(), [start]
	while todo:
		state = todo.pop()
		if state not in seen:
			seen.add(state)
			todo += [t.next_state for t in workflow.transitions if t.state == state]
	return seen


def is_danger(state):
	return frappe.get_cached_value("Workflow State", state, "style") == DANGER


def ended_states(workflow):
	if not workflow:
		return set()
	return {s.state for s in workflow.states if cint(s.doc_status) == 2 or is_danger(s.state)}


def approval_states(workflow):
	if not workflow:
		return set()
	doc_status = _doc_status(workflow)
	actions = defaultdict(set)
	for t in workflow.transitions:
		if doc_status.get(t.state) == 0 and t.next_state != t.state:
			actions[t.state].add(t.action)
	return {state for state, names in actions.items() if len(names) > 1}


def approved_states(workflow):
	if not workflow:
		return set()
	approval = approval_states(workflow)
	ended = ended_states(workflow)
	return {
		t.next_state
		for t in workflow.transitions
		if t.state in approval
		and t.next_state not in approval
		and t.next_state not in ended
		and not (_reachable(workflow, t.next_state) & approval)
	}


def approve_transitions(workflow):
	if not workflow:
		return []
	approval = approval_states(workflow)
	approved = approved_states(workflow)
	order = {s.state: i for i, s in enumerate(workflow.states)}
	return [
		t
		for t in workflow.transitions
		if t.state in approval
		and (
			t.next_state in approved
			or (t.next_state in approval and order.get(t.next_state, -1) > order.get(t.state, -1))
		)
	]


def rejected_states(workflow):
	if not workflow:
		return set()
	approval = approval_states(workflow)
	approve = {(t.state, t.action, t.next_state) for t in approve_transitions(workflow)}
	return {
		t.next_state
		for t in workflow.transitions
		if t.state in approval and (t.state, t.action, t.next_state) not in approve
	} - approval


def settled_states(workflow):
	"""Approved states and those that follow them (e.g. paid), short of ending."""
	if not workflow:
		return set()
	ended = ended_states(workflow)
	settled = set()
	for state in approved_states(workflow):
		settled |= _reachable(workflow, state)
	return settled - ended


def _submitted_moves(workflow):
	doc_status = _doc_status(workflow)
	return [
		t for t in workflow.transitions if doc_status.get(t.state) == 1 and doc_status.get(t.next_state) == 1
	]


def paid_states(workflow):
	return {t.next_state for t in _submitted_moves(workflow)} if workflow else set()


def pay_transitions(workflow):
	"""The moves that pay: from a submitted, not yet paid state into a paid one."""
	if not workflow:
		return []
	paid = paid_states(workflow)
	return [t for t in _submitted_moves(workflow) if t.state not in paid]


def payable_states(workflow):
	return {t.state for t in pay_transitions(workflow)}


def sql_not_in(column, states):
	"""SQL condition excluding states, or always-true when there are none."""
	if not states:
		return "1=1"
	values = ", ".join(frappe.db.escape(s) for s in sorted(states))
	return f"ifnull({column}, '') not in ({values})"
