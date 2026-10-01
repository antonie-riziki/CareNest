"""
WorkOS agent tests: memory, audit, human approval gates, credentials, demo flow.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.jobs.models import Job
from apps.profiles.models import EmployerProfile, WorkerProfile
from apps.wallet.models import Wallet
from apps.contracts.models import Contract

from apps.agentic_core.agent import CareNestAgent
from apps.agentic_core.models import AgentApproval, AgentDecision, AgentMemory, WorkCredential
from apps.agentic_core.policies import PolicyViolation, assert_human_gate, recommend, validate_prepared_call
from apps.agentic_core.services import execute_contract_call, issue_work_credential
from apps.agentic_core.state import EngagementStatus, InvalidTransition, can_transition, next_status
from apps.agentic_core import memory as memory_mod
from apps.agentic_core.omega_adapter import parse_requirement_rules, set_backend

User = get_user_model()


class StateMachineTests(TestCase):
    def test_valid_and_invalid_transitions(self):
        self.assertTrue(can_transition(EngagementStatus.CREATED, "fund"))
        self.assertFalse(can_transition(EngagementStatus.FUNDED, "fund"))
        self.assertFalse(can_transition(EngagementStatus.RELEASED, "approve_and_release"))
        self.assertEqual(next_status(EngagementStatus.CREATED, "fund"), EngagementStatus.FUNDED)
        with self.assertRaises(InvalidTransition):
            next_status(EngagementStatus.FUNDED, "fund")
        with self.assertRaises(InvalidTransition):
            next_status(EngagementStatus.RELEASED, "approve_and_release")
        self.assertFalse(can_transition(EngagementStatus.CREATED, "submit_work"))
        self.assertTrue(can_transition(EngagementStatus.FUNDED, "submit_work"))

    def test_human_gate_blocks_financial_calls(self):
        with self.assertRaises(PolicyViolation):
            assert_human_gate("fund", approved_by_human=False)
        with self.assertRaises(PolicyViolation):
            assert_human_gate("approve_and_release", approved_by_human=False)
        assert_human_gate("submit_work", approved_by_human=False)
        assert_human_gate("fund", approved_by_human=True)


class PolicyTests(TestCase):
    def test_submitted_requests_employer_approval(self):
        rec = recommend(
            status=EngagementStatus.SUBMITTED,
            has_pending_approval=False,
            worker_wallet_connected=True,
            employer_wallet_connected=True,
            funds_confirmed_on_chain=True,
            submission_confirmed_on_chain=True,
            credential_exists=False,
        )
        self.assertEqual(rec.decision_type, AgentDecision.Type.REQUEST_EMPLOYER_APPROVAL)
        self.assertTrue(rec.approval_required)
        self.assertTrue(rec.financial)
        self.assertEqual(rec.contract_function, "approve_and_release")

    def test_pending_approval_does_not_spam(self):
        rec = recommend(
            status=EngagementStatus.SUBMITTED,
            has_pending_approval=True,
            worker_wallet_connected=True,
            employer_wallet_connected=True,
            funds_confirmed_on_chain=True,
            submission_confirmed_on_chain=True,
            credential_exists=False,
        )
        self.assertEqual(rec.decision_type, AgentDecision.Type.NO_ACTION)


class ParserTests(TestCase):
    def test_demo_sentence(self):
        parsed = parse_requirement_rules("I need a full-time nanny in Westlands, KES 45,000/month.")
        self.assertEqual(parsed.job_type, "nanny")
        self.assertEqual(parsed.location, "Westlands")
        self.assertEqual(parsed.pay_amount, Decimal("45000"))
        self.assertEqual(parsed.pay_currency, "KES")
        self.assertEqual(parsed.schedule, "full-time")


@override_settings(CARENEST_FORCE_DEMO_MODE=True, CARENEST_CONTRACT_ID="", OMEGA_LLM_ENABLED=False)
class AgentFlowTests(TestCase):
    def setUp(self):
        set_backend(None)
        self.employer = User.objects.create_user(
            username="sarah@carenest.demo",
            email="sarah@carenest.demo",
            password="demo",
            first_name="Sarah",
            last_name="Otieno",
            role="employer",
        )
        EmployerProfile.objects.create(user=self.employer, rating=4.8)
        Wallet.objects.create(user=self.employer, balance=0, stellar_address="G" + "A" * 55)

        self.mary = User.objects.create_user(
            username="mary@carenest.demo",
            email="mary@carenest.demo",
            password="demo",
            first_name="Mary",
            last_name="Wanjiku",
            role="worker",
        )
        WorkerProfile.objects.create(
            user=self.mary,
            skills="nanny, childcare, first aid, meal preparation",
            rating=4.9,
            verified=True,
        )
        Wallet.objects.create(user=self.mary, balance=0, stellar_address="G" + "B" * 55)

    def test_intake_search_and_persistent_memory(self):
        agent = CareNestAgent.start(self.employer)
        result = agent.intake("I need a full-time nanny in Westlands, KES 45,000/month.")
        agent.end()
        self.assertTrue(result["candidates"])
        self.assertEqual(result["candidates"][0]["display_name"], "Mary Wanjiku")
        self.assertTrue(AgentDecision.objects.filter(decision_type=AgentDecision.Type.PARSE_REQUIREMENT).exists())
        self.assertTrue(AgentDecision.objects.filter(decision_type=AgentDecision.Type.RECOMMEND_WORKERS).exists())
        mem = memory_mod.load_for_user(self.employer)
        self.assertGreaterEqual(mem.version, 2)
        self.assertIn("pending_requirement", mem.engagement_context)

        # Session 2: new process-equivalent — load memory, do not invent state
        later = CareNestAgent.start(self.employer)
        answer = later.answer("What is happening with Mary's contract?")
        later.end()
        # No engagement yet, so the agent reports that fact rather than inventing one
        self.assertIn("could not find an engagement", answer["answer"].lower())

    def test_prepare_tick_approval_and_demo_execution(self):
        agent = CareNestAgent.start(self.employer)
        intake = agent.intake("I need a full-time nanny in Westlands, KES 45,000/month.")
        contract = agent.prepare_engagement(
            worker_id=self.mary.pk,
            job_id=intake["job"]["job_id"],
            requirement=intake["requirement"],
        )
        agent.end()
        self.assertEqual(contract.chain_status, EngagementStatus.DRAFT)
        self.assertEqual(contract.approval_status, contract.ApprovalStatus.PENDING_REVIEW)
        self.assertFalse(AgentApproval.objects.filter(engagement=contract, approval_type=AgentApproval.Type.CREATE_AGREEMENT).exists())

        contract.approval_status = contract.ApprovalStatus.APPROVED
        contract.approved_by = self.employer
        contract.save(update_fields=["approval_status", "approved_by", "updated_at"])
        CareNestAgent.start(self.employer).tick(contract)
        approval = AgentApproval.objects.get(engagement=contract, approval_type=AgentApproval.Type.CREATE_AGREEMENT)
        self.assertEqual(approval.status, AgentApproval.Status.PENDING)

        # Financial path without approval must fail
        from apps.agentic_core.policies import validate_prepared_call

        validate_prepared_call(contract.chain_status, "create_agreement")
        with self.assertRaises(PolicyViolation):
            execute_contract_call(
                contract,
                "create_agreement",
                {"client": contract.employer_wallet, "worker": contract.worker_wallet},
                signer_role="employer",
                actor=self.employer,
                approval=None,
            )

        from apps.agentic_core.audit import mark_approval

        mark_approval(approval, status=AgentApproval.Status.APPROVED, decided_by=self.employer)
        result = execute_contract_call(
            contract,
            "create_agreement",
            {
                "client": contract.employer_wallet,
                "worker": contract.worker_wallet,
                "token": "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC",
                "amount": contract.token_amount,
                "terms_hash": contract.terms_hash,
            },
            signer_role="employer",
            actor=self.employer,
            approval=approval,
        )
        self.assertEqual(result.tx.data_source, "DEMO_DATA")
        contract.refresh_from_db()
        self.assertEqual(contract.chain_status, EngagementStatus.CREATED)
        self.assertIsNotNone(contract.engagement_id)
        self.assertTrue(contract.create_tx_hash)

        # Memory survives a new session and answers about Mary
        later = CareNestAgent.start(self.employer)
        answer = later.answer("What is happening with Mary's contract?")
        later.end()
        self.assertEqual(answer["engagement"].pk, contract.pk)
        self.assertIn("Mary", answer["answer"])
        mem = memory_mod.load_for_engagement(contract)
        self.assertGreaterEqual(mem.version, 2)
        self.assertEqual(mem.contract_state, EngagementStatus.CREATED)

    def test_release_once_and_credential(self):
        job = Job.objects.create(
            employer=self.employer,
            title="Full-time Nanny in Westlands",
            description="nanny",
            location="Westlands",
            latitude=-1.26,
            longitude=36.80,
            pay=Decimal("45000"),
            job_type="nanny",
        )
        contract = Contract.objects.create(
            job=job,
            worker=self.mary,
            employer=self.employer,
            scope="childcare",
            status="approval_pending",
            chain_status=EngagementStatus.APPROVAL_PENDING,
            amount=Decimal("45000"),
            token_amount=45_000_0000,
            employer_wallet="G" + "A" * 55,
            worker_wallet="G" + "B" * 55,
            terms_hash="ab" * 32,
            engagement_id=7,
            data_source=Contract.DATA_SOURCE_DEMO,
            approval_status=Contract.ApprovalStatus.APPROVED,
        )
        decision = AgentDecision.objects.create(
            user=self.employer,
            engagement=contract,
            decision_type=AgentDecision.Type.REQUEST_EMPLOYER_APPROVAL,
            decision="release",
            approval_required=True,
            financial=True,
        )
        approval = AgentApproval.objects.create(
            decision=decision,
            engagement=contract,
            approval_type=AgentApproval.Type.RELEASE_PAYMENT,
            requested_from=self.employer,
            summary="release",
            prepared_action={"function": "approve_and_release"},
        )
        from apps.agentic_core.audit import mark_approval

        mark_approval(approval, status=AgentApproval.Status.APPROVED, decided_by=self.employer)
        first = execute_contract_call(
            contract, "approve_and_release", {"id": 7}, signer_role="employer", actor=self.employer, approval=approval
        )
        self.assertEqual(first.tx.status, "SUCCESS")
        contract.refresh_from_db()
        self.assertEqual(contract.chain_status, EngagementStatus.RELEASED)
        cred = WorkCredential.objects.get(engagement=contract)
        self.assertTrue(cred.employer_confirmation)
        self.assertEqual(cred.payment_status, WorkCredential.PaymentStatus.RELEASED)

        with self.assertRaises(PolicyViolation):
            validate_prepared_call(contract.chain_status, "approve_and_release")

        cred2, created = issue_work_credential(contract)
        self.assertFalse(created)
        self.assertEqual(cred2.pk, cred.pk)

    def test_audit_records_tool_calls(self):
        agent = CareNestAgent.start(self.employer)
        agent.intake("I need a nanny in Westlands, KES 45000/month")
        agent.end()
        from apps.agentic_core.models import AgentAction

        self.assertTrue(AgentAction.objects.filter(tool_name="create_job_draft").exists())
        self.assertTrue(AgentAction.objects.filter(tool_name="search_workers").exists())
