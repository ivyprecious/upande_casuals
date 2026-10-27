# Copyright (c) 2026, ivyprecious and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from upande_casuals import workflow as wf


def make_workflow(states, transitions):
	"""An unsaved Workflow; the helpers only read its states and transitions."""
	doc = frappe.new_doc("Workflow")
	doc.workflow_state_field = "workflow_state"
	for state, doc_status in states:
		doc.append("states", {"state": state, "doc_status": str(doc_status)})
	for state, action, next_state in transitions:
		doc.append("transitions", {"state": state, "action": action, "next_state": next_state})
	return doc


# Shapes copied from the live TSH site on 2026-10-27, renamed so no helper can
# pass by matching a familiar name.
BOOKING = make_workflow(
	[("B1", 0), ("B-no", 0), ("B-ok", 1), ("B-paid", 1), ("B-void", 2)],
	[
		("B1", "Yes", "B-ok"),
		("B1", "No", "B-no"),
		("B-no", "Again", "B1"),
		("B-ok", "Settle", "B-paid"),
		("B-paid", "Void", "B-void"),
	],
)
REQUEST = make_workflow(
	[("Start", 0), ("Step1", 0), ("Step2", 0), ("Step3", 0), ("Done", 0), ("Bounced", 0), ("Gone", 0)],
	[
		("Start", "Send", "Step1"),
		("Start", "Send", "Step2"),
		("Step1", "Yes", "Step2"),
		("Step1", "No", "Bounced"),
		("Step2", "Yes", "Step3"),
		("Step2", "No", "Bounced"),
		("Step3", "Yes", "Done"),
		("Step3", "No", "Bounced"),
		("Bounced", "Reopen", "Start"),
		("Done", "Drop", "Gone"),
	],
)


class TestWorkflowShape(IntegrationTestCase):
	def test_booking_workflow(self):
		self.assertEqual(wf.approval_states(BOOKING), {"B1"})
		self.assertEqual(wf.approved_states(BOOKING), {"B-ok"})
		self.assertEqual(wf.rejected_states(BOOKING), {"B-no"})
		self.assertEqual([t.action for t in wf.approve_transitions(BOOKING)], ["Yes"])
		self.assertEqual(wf.paid_states(BOOKING), {"B-paid"})
		self.assertEqual(wf.payable_states(BOOKING), {"B-ok"})
		self.assertEqual(wf.ended_states(BOOKING), {"B-void"})
		self.assertEqual(wf.settled_states(BOOKING), {"B-ok", "B-paid"})

	def test_request_workflow(self):
		self.assertEqual(wf.approval_states(REQUEST), {"Step1", "Step2", "Step3"})
		self.assertEqual(wf.approved_states(REQUEST), {"Done"})
		self.assertEqual(wf.rejected_states(REQUEST), {"Bounced"})
		self.assertEqual({t.action for t in wf.approve_transitions(REQUEST)}, {"Yes"})
		self.assertEqual(wf.paid_states(REQUEST), set())

	def test_no_workflow(self):
		for helper in (wf.approval_states, wf.approved_states, wf.rejected_states, wf.paid_states):
			self.assertEqual(helper(None), set())
		self.assertEqual(wf.sql_not_in("b.workflow_state", set()), "1=1")
