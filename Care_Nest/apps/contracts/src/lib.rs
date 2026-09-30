//! CareNest WorkOS — Soroban escrow agreement contract.
//!
//! One contract instance manages many independent domestic-work engagements.
//! Every engagement is a strict state machine; invalid transitions are rejected
//! with a typed error and every successful transition emits an event so that
//! off-chain indexers (the CareNest Django agent) can reconstruct history.
//!
//! Privacy: only public wallet addresses, engagement identifiers, amounts,
//! statuses, timestamps and 32-byte hashes are ever stored on-chain.
//!
//! Lifecycle
//! ---------
//! CREATED ──fund──▶ FUNDED ──start_work──▶ WORKING ──submit_work──▶ SUBMITTED
//!    │                 │                        │                        │
//!    │cancel           │cancel / dispute        │dispute                 │request_approval
//!    ▼                 ▼                        ▼                        ▼
//! CANCELLED        CANCELLED/DISPUTED        DISPUTED            APPROVAL_PENDING
//!                                                                      │ approve_and_release
//!                                                                      ▼
//!                                                                  RELEASED ──issue_credential
//!
//! DISPUTED ──resolve_dispute(arbiter)──▶ RELEASED | CANCELLED (refund)
//!
//! Funds can only ever leave the contract once per engagement because every
//! transfer is guarded by a transition into a terminal state (RELEASED or
//! CANCELLED), and terminal states accept no further transitions.

#![no_std]

use soroban_sdk::{
    contract, contracterror, contractimpl, contracttype, token, Address, BytesN, Env, Symbol,
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Lifecycle status of an engagement.
#[contracttype]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    Created = 0,
    Funded = 1,
    Working = 2,
    Submitted = 3,
    ApprovalPending = 4,
    Disputed = 5,
    Released = 6,
    Cancelled = 7,
}

/// On-chain representation of one engagement. No personal data.
#[contracttype]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Agreement {
    pub id: u64,
    pub client: Address,
    pub worker: Address,
    pub token: Address,
    pub amount: i128,
    pub status: Status,
    /// Hash of the off-chain contract terms (scope, schedule, pay) — keccak/sha256.
    pub terms_hash: BytesN<32>,
    /// Hash of the worker's completion report (set on submit_work).
    pub work_hash: Option<BytesN<32>>,
    /// Hash of the issued Work Credential (set on issue_credential).
    pub credential_hash: Option<BytesN<32>>,
    pub created_at: u64,
    pub funded_at: u64,
    pub submitted_at: u64,
    pub released_at: u64,
    pub updated_at: u64,
}

#[contracttype]
#[derive(Clone)]
pub enum DataKey {
    /// Address allowed to resolve disputes (CareNest arbiter / admin).
    Arbiter,
    /// Monotonic counter used to allocate engagement identifiers.
    Counter,
    /// Agreement record keyed by engagement id.
    Agreement(u64),
}

#[contracterror]
#[derive(Copy, Clone, Debug, Eq, PartialEq, PartialOrd, Ord)]
#[repr(u32)]
pub enum Error {
    AlreadyInitialized = 1,
    NotInitialized = 2,
    AgreementNotFound = 3,
    InvalidTransition = 4,
    Unauthorized = 5,
    InvalidAmount = 6,
    SameParty = 7,
    CredentialAlreadyIssued = 8,
}

// Persistent entries are bumped so demo data does not expire quickly on testnet.
const LEDGERS_PER_DAY: u32 = 17_280; // ~5s ledgers
const BUMP_THRESHOLD: u32 = LEDGERS_PER_DAY * 7;
const BUMP_TO: u32 = LEDGERS_PER_DAY * 30;

// ---------------------------------------------------------------------------
// Contract
// ---------------------------------------------------------------------------

#[contract]
pub struct WorkContract;

#[contractimpl]
impl WorkContract {
    /// One-time initialisation. `arbiter` is the only address that can resolve disputes.
    pub fn initialize(env: Env, arbiter: Address) -> Result<(), Error> {
        if env.storage().instance().has(&DataKey::Arbiter) {
            return Err(Error::AlreadyInitialized);
        }
        arbiter.require_auth();
        env.storage().instance().set(&DataKey::Arbiter, &arbiter);
        env.storage().instance().set(&DataKey::Counter, &0u64);
        env.storage().instance().extend_ttl(BUMP_THRESHOLD, BUMP_TO);
        Ok(())
    }

    /// Employer (client) creates a new engagement. Returns the engagement id.
    ///
    /// Emits `agreement_created(id) -> (client, worker, token, amount, terms_hash)`.
    pub fn create_agreement(
        env: Env,
        client: Address,
        worker: Address,
        token: Address,
        amount: i128,
        terms_hash: BytesN<32>,
    ) -> Result<u64, Error> {
        Self::require_initialized(&env)?;
        client.require_auth();
        if amount <= 0 {
            return Err(Error::InvalidAmount);
        }
        if client == worker {
            return Err(Error::SameParty);
        }

        let id: u64 = env.storage().instance().get(&DataKey::Counter).unwrap_or(0) + 1;
        env.storage().instance().set(&DataKey::Counter, &id);

        let now = env.ledger().timestamp();
        let mut agreement = Agreement {
            id,
            client: client.clone(),
            worker: worker.clone(),
            token: token.clone(),
            amount,
            status: Status::Created,
            terms_hash: terms_hash.clone(),
            work_hash: None,
            credential_hash: None,
            created_at: now,
            funded_at: 0,
            submitted_at: 0,
            released_at: 0,
            updated_at: now,
        };
        Self::save(&env, &mut agreement);

        env.events().publish(
            (Symbol::new(&env, "agreement_created"), id),
            (client, worker, token, amount, terms_hash),
        );
        Ok(id)
    }

    /// Client moves escrow funds into the contract. CREATED -> FUNDED.
    /// A second call is rejected because the status is no longer CREATED.
    ///
    /// Emits `agreement_funded(id) -> (client, amount)`.
    pub fn fund(env: Env, id: u64) -> Result<(), Error> {
        let mut a = Self::load(&env, id)?;
        a.client.require_auth();
        Self::transition(&mut a, &[Status::Created], Status::Funded)?;

        token::TokenClient::new(&env, &a.token).transfer(
            &a.client,
            &env.current_contract_address(),
            &a.amount,
        );

        a.funded_at = env.ledger().timestamp();
        Self::save(&env, &mut a);
        env.events().publish(
            (Symbol::new(&env, "agreement_funded"), id),
            (a.client.clone(), a.amount),
        );
        Ok(())
    }

    /// Worker acknowledges the engagement has started. FUNDED -> WORKING.
    ///
    /// Emits `work_started(id) -> worker`.
    pub fn start_work(env: Env, id: u64) -> Result<(), Error> {
        let mut a = Self::load(&env, id)?;
        a.worker.require_auth();
        Self::transition(&mut a, &[Status::Funded], Status::Working)?;
        Self::save(&env, &mut a);
        env.events()
            .publish((Symbol::new(&env, "work_started"), id), a.worker.clone());
        Ok(())
    }

    /// Worker submits completion with a hash of the completion report.
    /// FUNDED | WORKING -> SUBMITTED.
    ///
    /// Emits `work_submitted(id) -> (worker, work_hash)`.
    pub fn submit_work(env: Env, id: u64, work_hash: BytesN<32>) -> Result<(), Error> {
        let mut a = Self::load(&env, id)?;
        a.worker.require_auth();
        Self::transition(&mut a, &[Status::Funded, Status::Working], Status::Submitted)?;
        a.work_hash = Some(work_hash.clone());
        a.submitted_at = env.ledger().timestamp();
        Self::save(&env, &mut a);
        env.events().publish(
            (Symbol::new(&env, "work_submitted"), id),
            (a.worker.clone(), work_hash),
        );
        Ok(())
    }

    /// Either party (typically triggered by the worker or the CareNest agent acting
    /// with the worker's signature) flags that the employer should now review.
    /// SUBMITTED -> APPROVAL_PENDING. This is informational; it moves no funds.
    ///
    /// Emits `approval_requested(id) -> requester`.
    pub fn request_approval(env: Env, id: u64, requester: Address) -> Result<(), Error> {
        let mut a = Self::load(&env, id)?;
        requester.require_auth();
        if requester != a.client && requester != a.worker {
            return Err(Error::Unauthorized);
        }
        Self::transition(&mut a, &[Status::Submitted], Status::ApprovalPending)?;
        Self::save(&env, &mut a);
        env.events()
            .publish((Symbol::new(&env, "approval_requested"), id), requester);
        Ok(())
    }

    /// Client explicitly approves the work and releases escrow to the worker.
    /// SUBMITTED | APPROVAL_PENDING -> RELEASED. Funds move exactly once.
    ///
    /// Emits `payment_released(id) -> (worker, amount)`.
    pub fn approve_and_release(env: Env, id: u64) -> Result<(), Error> {
        let mut a = Self::load(&env, id)?;
        a.client.require_auth();
        Self::transition(
            &mut a,
            &[Status::Submitted, Status::ApprovalPending],
            Status::Released,
        )?;

        token::TokenClient::new(&env, &a.token).transfer(
            &env.current_contract_address(),
            &a.worker,
            &a.amount,
        );

        a.released_at = env.ledger().timestamp();
        Self::save(&env, &mut a);
        env.events().publish(
            (Symbol::new(&env, "payment_released"), id),
            (a.worker.clone(), a.amount),
        );
        Ok(())
    }

    /// Either party opens a dispute while funds are in escrow.
    /// FUNDED | WORKING | SUBMITTED | APPROVAL_PENDING -> DISPUTED.
    ///
    /// Emits `agreement_disputed(id) -> (opener, reason_hash)`.
    pub fn dispute(
        env: Env,
        id: u64,
        opener: Address,
        reason_hash: BytesN<32>,
    ) -> Result<(), Error> {
        let mut a = Self::load(&env, id)?;
        opener.require_auth();
        if opener != a.client && opener != a.worker {
            return Err(Error::Unauthorized);
        }
        Self::transition(
            &mut a,
            &[
                Status::Funded,
                Status::Working,
                Status::Submitted,
                Status::ApprovalPending,
            ],
            Status::Disputed,
        )?;
        Self::save(&env, &mut a);
        env.events().publish(
            (Symbol::new(&env, "agreement_disputed"), id),
            (opener, reason_hash),
        );
        Ok(())
    }

    /// Arbiter resolves a dispute. `pay_worker == true` releases escrow to the
    /// worker (-> RELEASED), otherwise escrow is refunded to the client (-> CANCELLED).
    ///
    /// Emits `dispute_resolved(id) -> pay_worker` followed by
    /// `payment_released` or `agreement_cancelled`.
    pub fn resolve_dispute(env: Env, id: u64, pay_worker: bool) -> Result<(), Error> {
        let arbiter: Address = env
            .storage()
            .instance()
            .get(&DataKey::Arbiter)
            .ok_or(Error::NotInitialized)?;
        arbiter.require_auth();

        let mut a = Self::load(&env, id)?;
        let target = if pay_worker {
            Status::Released
        } else {
            Status::Cancelled
        };
        Self::transition(&mut a, &[Status::Disputed], target)?;

        let recipient = if pay_worker {
            a.worker.clone()
        } else {
            a.client.clone()
        };
        token::TokenClient::new(&env, &a.token).transfer(
            &env.current_contract_address(),
            &recipient,
            &a.amount,
        );

        let now = env.ledger().timestamp();
        if pay_worker {
            a.released_at = now;
        }
        Self::save(&env, &mut a);

        env.events()
            .publish((Symbol::new(&env, "dispute_resolved"), id), pay_worker);
        if pay_worker {
            env.events().publish(
                (Symbol::new(&env, "payment_released"), id),
                (a.worker.clone(), a.amount),
            );
        } else {
            env.events().publish(
                (Symbol::new(&env, "agreement_cancelled"), id),
                (a.client.clone(), a.amount),
            );
        }
        Ok(())
    }

    /// Client cancels before any work has been submitted.
    /// CREATED -> CANCELLED (nothing to refund), FUNDED -> CANCELLED (refund).
    ///
    /// Emits `agreement_cancelled(id) -> (client, refunded_amount)`.
    pub fn cancel(env: Env, id: u64) -> Result<(), Error> {
        let mut a = Self::load(&env, id)?;
        a.client.require_auth();
        let was_funded = a.status == Status::Funded;
        Self::transition(&mut a, &[Status::Created, Status::Funded], Status::Cancelled)?;

        let refunded = if was_funded {
            token::TokenClient::new(&env, &a.token).transfer(
                &env.current_contract_address(),
                &a.client,
                &a.amount,
            );
            a.amount
        } else {
            0
        };

        Self::save(&env, &mut a);
        env.events().publish(
            (Symbol::new(&env, "agreement_cancelled"), id),
            (a.client.clone(), refunded),
        );
        Ok(())
    }

    /// Client anchors the Work Credential hash for a RELEASED engagement.
    /// This is the employer's on-chain confirmation of completed work. Once only.
    ///
    /// Emits `credential_issued(id) -> (worker, credential_hash)`.
    pub fn issue_credential(env: Env, id: u64, credential_hash: BytesN<32>) -> Result<(), Error> {
        let mut a = Self::load(&env, id)?;
        a.client.require_auth();
        if a.status != Status::Released {
            return Err(Error::InvalidTransition);
        }
        if a.credential_hash.is_some() {
            return Err(Error::CredentialAlreadyIssued);
        }
        a.credential_hash = Some(credential_hash.clone());
        a.updated_at = env.ledger().timestamp();
        Self::save(&env, &mut a);
        env.events().publish(
            (Symbol::new(&env, "credential_issued"), id),
            (a.worker.clone(), credential_hash),
        );
        Ok(())
    }

    // ----------------------------- read-only ------------------------------

    pub fn get_agreement(env: Env, id: u64) -> Result<Agreement, Error> {
        Self::load(&env, id)
    }

    pub fn get_status(env: Env, id: u64) -> Result<Status, Error> {
        Ok(Self::load(&env, id)?.status)
    }

    pub fn get_count(env: Env) -> u64 {
        env.storage().instance().get(&DataKey::Counter).unwrap_or(0)
    }

    pub fn get_arbiter(env: Env) -> Result<Address, Error> {
        env.storage()
            .instance()
            .get(&DataKey::Arbiter)
            .ok_or(Error::NotInitialized)
    }

    // ------------------------------ internals -----------------------------

    fn require_initialized(env: &Env) -> Result<(), Error> {
        if env.storage().instance().has(&DataKey::Arbiter) {
            Ok(())
        } else {
            Err(Error::NotInitialized)
        }
    }

    fn load(env: &Env, id: u64) -> Result<Agreement, Error> {
        let key = DataKey::Agreement(id);
        let a: Agreement = env
            .storage()
            .persistent()
            .get(&key)
            .ok_or(Error::AgreementNotFound)?;
        env.storage()
            .persistent()
            .extend_ttl(&key, BUMP_THRESHOLD, BUMP_TO);
        Ok(a)
    }

    fn save(env: &Env, a: &mut Agreement) {
        a.updated_at = env.ledger().timestamp();
        let key = DataKey::Agreement(a.id);
        env.storage().persistent().set(&key, a);
        env.storage()
            .persistent()
            .extend_ttl(&key, BUMP_THRESHOLD, BUMP_TO);
        env.storage().instance().extend_ttl(BUMP_THRESHOLD, BUMP_TO);
    }

    /// Validate and apply a state transition. Terminal states never appear in
    /// `allowed_from`, so RELEASED / CANCELLED can never be left.
    fn transition(a: &mut Agreement, allowed_from: &[Status], to: Status) -> Result<(), Error> {
        if !allowed_from.contains(&a.status) {
            return Err(Error::InvalidTransition);
        }
        a.status = to;
        Ok(())
    }
}

#[cfg(test)]
mod test;
