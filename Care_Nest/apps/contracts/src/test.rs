#![cfg(test)]
extern crate std;

use super::*;
use soroban_sdk::{
    testutils::{Address as _, Events, Ledger},
    token::{StellarAssetClient, TokenClient},
    Address, BytesN, Env, Symbol, TryFromVal, Val,
};

const AMOUNT: i128 = 45_000_0000000; // 45,000 units with 7 decimals

struct Fixture {
    env: Env,
    contract_id: Address,
    client: WorkContractClient<'static>,
    token_addr: Address,
    token: TokenClient<'static>,
    arbiter: Address,
    employer: Address,
    worker: Address,
}

fn setup() -> Fixture {
    let env = Env::default();
    env.mock_all_auths();
    env.ledger().with_mut(|l| l.timestamp = 1_700_000_000);

    let contract_id = env.register(WorkContract, ());
    let client = WorkContractClient::new(&env, &contract_id);

    let arbiter = Address::generate(&env);
    let employer = Address::generate(&env);
    let worker = Address::generate(&env);

    let token_admin = Address::generate(&env);
    let sac = env.register_stellar_asset_contract_v2(token_admin.clone());
    let token_addr = sac.address();
    StellarAssetClient::new(&env, &token_addr).mint(&employer, &(AMOUNT * 10));
    let token = TokenClient::new(&env, &token_addr);

    client.initialize(&arbiter);

    Fixture {
        env,
        contract_id,
        client,
        token_addr,
        token,
        arbiter,
        employer,
        worker,
    }
}

fn terms_hash(env: &Env) -> BytesN<32> {
    BytesN::from_array(env, &[7u8; 32])
}

fn create(f: &Fixture) -> u64 {
    f.client.create_agreement(
        &f.employer,
        &f.worker,
        &f.token_addr,
        &AMOUNT,
        &terms_hash(&f.env),
    )
}

/// Collect the event topic names emitted by the last successful invocation.
fn event_names(f: &Fixture) -> std::vec::Vec<Symbol> {
    f.env
        .events()
        .all()
        .filter_by_contract(&f.contract_id)
        .events()
        .iter()
        .filter_map(|event| {
            let soroban_sdk::xdr::ContractEventBody::V0(body) = &event.body;
            let first = body.topics.get(0)?;
            let val = Val::try_from_val(&f.env, first).ok()?;
            Symbol::try_from_val(&f.env, &val).ok()
        })
        .collect()
}

fn sym(f: &Fixture, s: &str) -> Symbol {
    Symbol::new(&f.env, s)
}

fn last_event(f: &Fixture) -> Symbol {
    event_names(f).last().unwrap().clone()
}

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------

#[test]
fn initialize_sets_arbiter_and_rejects_double_init() {
    let f = setup();
    assert_eq!(f.client.get_arbiter(), f.arbiter);
    assert_eq!(f.client.get_count(), 0);
    assert_eq!(
        f.client.try_initialize(&f.arbiter),
        Err(Ok(Error::AlreadyInitialized))
    );
}

#[test]
fn create_requires_initialization() {
    let env = Env::default();
    env.mock_all_auths();
    let id = env.register(WorkContract, ());
    let client = WorkContractClient::new(&env, &id);
    let a = Address::generate(&env);
    let b = Address::generate(&env);
    let t = Address::generate(&env);
    assert_eq!(
        client.try_create_agreement(&a, &b, &t, &10, &BytesN::from_array(&env, &[0u8; 32])),
        Err(Ok(Error::NotInitialized))
    );
}

#[test]
fn create_agreement_allocates_ids_and_emits_event() {
    let f = setup();
    let id1 = create(&f);
    assert_eq!(last_event(&f), sym(&f, "agreement_created"));
    let id2 = create(&f);
    assert_eq!(id1, 1);
    assert_eq!(id2, 2);
    assert_eq!(f.client.get_count(), 2);

    let a = f.client.get_agreement(&id1);
    assert_eq!(a.status, Status::Created);
    assert_eq!(a.amount, AMOUNT);
    assert_eq!(a.client, f.employer);
    assert_eq!(a.worker, f.worker);
    assert_eq!(a.created_at, 1_700_000_000);
}

#[test]
fn create_rejects_bad_inputs() {
    let f = setup();
    assert_eq!(
        f.client.try_create_agreement(
            &f.employer,
            &f.worker,
            &f.token_addr,
            &0,
            &terms_hash(&f.env)
        ),
        Err(Ok(Error::InvalidAmount))
    );
    assert_eq!(
        f.client.try_create_agreement(
            &f.employer,
            &f.employer,
            &f.token_addr,
            &AMOUNT,
            &terms_hash(&f.env)
        ),
        Err(Ok(Error::SameParty))
    );
    assert_eq!(
        f.client.try_get_agreement(&99),
        Err(Ok(Error::AgreementNotFound))
    );
}

// ---------------------------------------------------------------------------
// Funding
// ---------------------------------------------------------------------------

#[test]
fn fund_moves_tokens_into_escrow_once() {
    let f = setup();
    let id = create(&f);
    let before = f.token.balance(&f.employer);

    f.client.fund(&id);
    assert_eq!(last_event(&f), sym(&f, "agreement_funded"));

    assert_eq!(f.token.balance(&f.employer), before - AMOUNT);
    assert_eq!(f.token.balance(&f.contract_id), AMOUNT);
    let a = f.client.get_agreement(&id);
    assert_eq!(a.status, Status::Funded);
    assert!(a.funded_at > 0);

    // Repeated funding must be rejected and must not move more tokens.
    assert_eq!(f.client.try_fund(&id), Err(Ok(Error::InvalidTransition)));
    assert_eq!(f.token.balance(&f.contract_id), AMOUNT);
}

#[test]
fn funding_one_agreement_does_not_affect_another() {
    let f = setup();
    let id1 = create(&f);
    let id2 = create(&f);
    f.client.fund(&id1);
    assert_eq!(f.client.get_status(&id1), Status::Funded);
    assert_eq!(f.client.get_status(&id2), Status::Created);
    assert_eq!(f.token.balance(&f.contract_id), AMOUNT);
}

// ---------------------------------------------------------------------------
// Work submission
// ---------------------------------------------------------------------------

#[test]
fn submit_work_from_funded_or_working() {
    let f = setup();
    let id = create(&f);
    f.client.fund(&id);
    f.client.start_work(&id);
    assert_eq!(f.client.get_status(&id), Status::Working);

    let wh = BytesN::from_array(&f.env, &[9u8; 32]);
    f.client.submit_work(&id, &wh);
    assert_eq!(last_event(&f), sym(&f, "work_submitted"));
    let a = f.client.get_agreement(&id);
    assert_eq!(a.status, Status::Submitted);
    assert_eq!(a.work_hash, Some(wh));

    // direct FUNDED -> SUBMITTED is also allowed
    let id2 = create(&f);
    f.client.fund(&id2);
    f.client.submit_work(&id2, &BytesN::from_array(&f.env, &[1u8; 32]));
    assert_eq!(f.client.get_status(&id2), Status::Submitted);
}

#[test]
fn submit_work_rejected_when_not_funded() {
    let f = setup();
    let id = create(&f);
    let wh = BytesN::from_array(&f.env, &[9u8; 32]);
    assert_eq!(
        f.client.try_submit_work(&id, &wh),
        Err(Ok(Error::InvalidTransition))
    );
    // and double submission is rejected
    f.client.fund(&id);
    f.client.submit_work(&id, &wh);
    assert_eq!(
        f.client.try_submit_work(&id, &wh),
        Err(Ok(Error::InvalidTransition))
    );
}

#[test]
fn start_work_only_from_funded() {
    let f = setup();
    let id = create(&f);
    assert_eq!(f.client.try_start_work(&id), Err(Ok(Error::InvalidTransition)));
    f.client.fund(&id);
    f.client.start_work(&id);
    assert_eq!(f.client.try_start_work(&id), Err(Ok(Error::InvalidTransition)));
}

// ---------------------------------------------------------------------------
// Approval & release
// ---------------------------------------------------------------------------

#[test]
fn request_approval_then_release_pays_worker_exactly_once() {
    let f = setup();
    let id = create(&f);
    f.client.fund(&id);
    f.client
        .submit_work(&id, &BytesN::from_array(&f.env, &[9u8; 32]));
    f.client.request_approval(&id, &f.worker);
    assert_eq!(last_event(&f), sym(&f, "approval_requested"));
    assert_eq!(f.client.get_status(&id), Status::ApprovalPending);

    let worker_before = f.token.balance(&f.worker);
    f.client.approve_and_release(&id);
    assert_eq!(last_event(&f), sym(&f, "payment_released"));
    assert_eq!(f.token.balance(&f.worker), worker_before + AMOUNT);
    assert_eq!(f.token.balance(&f.contract_id), 0);
    let a = f.client.get_agreement(&id);
    assert_eq!(a.status, Status::Released);
    assert!(a.released_at > 0);

    // Second release must fail and must not move funds.
    assert_eq!(
        f.client.try_approve_and_release(&id),
        Err(Ok(Error::InvalidTransition))
    );
    assert_eq!(f.token.balance(&f.worker), worker_before + AMOUNT);
    assert_eq!(f.token.balance(&f.contract_id), 0);
}

#[test]
fn release_directly_from_submitted_is_allowed() {
    let f = setup();
    let id = create(&f);
    f.client.fund(&id);
    f.client
        .submit_work(&id, &BytesN::from_array(&f.env, &[9u8; 32]));
    f.client.approve_and_release(&id);
    assert_eq!(f.client.get_status(&id), Status::Released);
}

#[test]
fn release_rejected_before_submission() {
    let f = setup();
    let id = create(&f);
    assert_eq!(
        f.client.try_approve_and_release(&id),
        Err(Ok(Error::InvalidTransition))
    );
    f.client.fund(&id);
    assert_eq!(
        f.client.try_approve_and_release(&id),
        Err(Ok(Error::InvalidTransition))
    );
    assert_eq!(f.token.balance(&f.contract_id), AMOUNT);
}

#[test]
fn request_approval_rejects_third_party() {
    let f = setup();
    let id = create(&f);
    f.client.fund(&id);
    f.client
        .submit_work(&id, &BytesN::from_array(&f.env, &[9u8; 32]));
    let stranger = Address::generate(&f.env);
    assert_eq!(
        f.client.try_request_approval(&id, &stranger),
        Err(Ok(Error::Unauthorized))
    );
}

// ---------------------------------------------------------------------------
// Disputes
// ---------------------------------------------------------------------------

#[test]
fn dispute_then_resolve_to_worker() {
    let f = setup();
    let id = create(&f);
    f.client.fund(&id);
    let reason = BytesN::from_array(&f.env, &[3u8; 32]);
    f.client.dispute(&id, &f.worker, &reason);
    assert_eq!(last_event(&f), sym(&f, "agreement_disputed"));
    assert_eq!(f.client.get_status(&id), Status::Disputed);

    // While disputed, nobody can release or cancel.
    assert_eq!(
        f.client.try_approve_and_release(&id),
        Err(Ok(Error::InvalidTransition))
    );
    assert_eq!(f.client.try_cancel(&id), Err(Ok(Error::InvalidTransition)));

    let worker_before = f.token.balance(&f.worker);
    f.client.resolve_dispute(&id, &true);
    let names = event_names(&f);
    assert_eq!(names[names.len() - 2], sym(&f, "dispute_resolved"));
    assert_eq!(names[names.len() - 1], sym(&f, "payment_released"));
    assert_eq!(f.client.get_status(&id), Status::Released);
    assert_eq!(f.token.balance(&f.worker), worker_before + AMOUNT);

    assert_eq!(
        f.client.try_resolve_dispute(&id, &true),
        Err(Ok(Error::InvalidTransition))
    );
}

#[test]
fn dispute_then_resolve_refund_to_client() {
    let f = setup();
    let id = create(&f);
    f.client.fund(&id);
    let employer_before = f.token.balance(&f.employer);
    f.client
        .dispute(&id, &f.employer, &BytesN::from_array(&f.env, &[3u8; 32]));
    f.client.resolve_dispute(&id, &false);
    assert_eq!(last_event(&f), sym(&f, "agreement_cancelled"));
    assert_eq!(f.client.get_status(&id), Status::Cancelled);
    assert_eq!(f.token.balance(&f.employer), employer_before + AMOUNT);
    assert_eq!(f.token.balance(&f.contract_id), 0);
}

#[test]
fn dispute_rejected_when_unfunded_or_terminal() {
    let f = setup();
    let id = create(&f);
    let reason = BytesN::from_array(&f.env, &[3u8; 32]);
    assert_eq!(
        f.client.try_dispute(&id, &f.worker, &reason),
        Err(Ok(Error::InvalidTransition))
    );
    let stranger = Address::generate(&f.env);
    f.client.fund(&id);
    assert_eq!(
        f.client.try_dispute(&id, &stranger, &reason),
        Err(Ok(Error::Unauthorized))
    );
    f.client
        .submit_work(&id, &BytesN::from_array(&f.env, &[9u8; 32]));
    f.client.approve_and_release(&id);
    assert_eq!(
        f.client.try_dispute(&id, &f.worker, &reason),
        Err(Ok(Error::InvalidTransition))
    );
}

// ---------------------------------------------------------------------------
// Cancellation
// ---------------------------------------------------------------------------

#[test]
fn cancel_created_and_funded_agreements() {
    let f = setup();
    let id1 = create(&f);
    f.client.cancel(&id1);
    assert_eq!(last_event(&f), sym(&f, "agreement_cancelled"));
    assert_eq!(f.client.get_status(&id1), Status::Cancelled);

    let id2 = create(&f);
    f.client.fund(&id2);
    let before = f.token.balance(&f.employer);
    f.client.cancel(&id2);
    assert_eq!(f.client.get_status(&id2), Status::Cancelled);
    assert_eq!(f.token.balance(&f.employer), before + AMOUNT);
    assert_eq!(f.token.balance(&f.contract_id), 0);

    // terminal: nothing further allowed
    assert_eq!(f.client.try_fund(&id2), Err(Ok(Error::InvalidTransition)));
    assert_eq!(f.client.try_cancel(&id2), Err(Ok(Error::InvalidTransition)));
}

#[test]
fn cancel_rejected_after_submission() {
    let f = setup();
    let id = create(&f);
    f.client.fund(&id);
    f.client
        .submit_work(&id, &BytesN::from_array(&f.env, &[9u8; 32]));
    assert_eq!(f.client.try_cancel(&id), Err(Ok(Error::InvalidTransition)));
    assert_eq!(f.token.balance(&f.contract_id), AMOUNT);
}

// ---------------------------------------------------------------------------
// Credential
// ---------------------------------------------------------------------------

#[test]
fn credential_issued_only_after_release_and_only_once() {
    let f = setup();
    let id = create(&f);
    let ch = BytesN::from_array(&f.env, &[5u8; 32]);
    assert_eq!(
        f.client.try_issue_credential(&id, &ch),
        Err(Ok(Error::InvalidTransition))
    );
    f.client.fund(&id);
    f.client
        .submit_work(&id, &BytesN::from_array(&f.env, &[9u8; 32]));
    f.client.approve_and_release(&id);

    f.client.issue_credential(&id, &ch);
    assert_eq!(last_event(&f), sym(&f, "credential_issued"));
    let a = f.client.get_agreement(&id);
    assert_eq!(a.credential_hash, Some(ch.clone()));

    assert_eq!(
        f.client.try_issue_credential(&id, &ch),
        Err(Ok(Error::CredentialAlreadyIssued))
    );
}

// ---------------------------------------------------------------------------
// Event stream over the full happy path
// ---------------------------------------------------------------------------

#[test]
fn full_lifecycle_emits_expected_event_sequence() {
    let f = setup();
    let mut names = std::vec::Vec::new();
    let id = create(&f);
    names.extend(event_names(&f));
    f.client.fund(&id);
    names.extend(event_names(&f));
    f.client.start_work(&id);
    names.extend(event_names(&f));
    f.client
        .submit_work(&id, &BytesN::from_array(&f.env, &[9u8; 32]));
    names.extend(event_names(&f));
    f.client.request_approval(&id, &f.worker);
    names.extend(event_names(&f));
    f.client.approve_and_release(&id);
    names.extend(event_names(&f));
    f.client
        .issue_credential(&id, &BytesN::from_array(&f.env, &[5u8; 32]));
    names.extend(event_names(&f));
    let expected = [
        "agreement_created",
        "agreement_funded",
        "work_started",
        "work_submitted",
        "approval_requested",
        "payment_released",
        "credential_issued",
    ];
    assert_eq!(names.len(), expected.len());
    for (got, want) in names.iter().zip(expected.iter()) {
        assert_eq!(got, &sym(&f, want));
    }

    // The engagement id is the second topic of every event.
    let events = f.env.events().all().filter_by_contract(&f.contract_id);
    let last = events.events().last().unwrap();
    let soroban_sdk::xdr::ContractEventBody::V0(body) = &last.body;
    let id_topic = body.topics.get(1).expect("engagement id topic");
    let id_val = Val::try_from_val(&f.env, id_topic).expect("topic val");
    assert_eq!(u64::try_from_val(&f.env, &id_val).unwrap(), id);
}
