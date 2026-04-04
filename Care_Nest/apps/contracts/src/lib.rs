#![no_std]

use soroban_sdk::{
    contract, contractimpl, contracttype,
    Address, Env, Symbol, token,
};

#[contracttype]
#[derive(Clone)]
pub struct Agreement {
    pub client: Address,
    pub worker: Address,
    pub amount: i128,
    pub funded: bool,
    pub completed: bool,
}

#[contract]
pub struct WorkContract;

#[contractimpl]
impl WorkContract {

    // Initialize agreement
    pub fn init(
        env: Env,
        client: Address,
        worker: Address,
        amount: i128,
    ) {

        client.require_auth();

        let agreement = Agreement {
            client,
            worker,
            amount,
            funded: false,
            completed: false,
        };

        env.storage().instance().set(&Symbol::new(&env, "agreement"), &agreement);
    }

    // Client funds the contract
    pub fn fund(env: Env, token_address: Address) {

        let mut agreement: Agreement =
            env.storage().instance().get(&Symbol::new(&env, "agreement")).unwrap();

        agreement.client.require_auth();

        let token_client = token::Client::new(&env, &token_address);

        token_client.transfer(
            &agreement.client,
            &env.current_contract_address(),
            &agreement.amount,
        );

        agreement.funded = true;

        env.storage().instance().set(&Symbol::new(&env, "agreement"), &agreement);
    }

    // Worker marks work as completed
    pub fn submit_work(env: Env) {

        let mut agreement: Agreement =
            env.storage().instance().get(&Symbol::new(&env, "agreement")).unwrap();

        agreement.worker.require_auth();

        agreement.completed = true;

        env.storage().instance().set(&Symbol::new(&env, "agreement"), &agreement);
    }

    // Client approves and releases payment
    pub fn approve_and_pay(env: Env, token_address: Address) {

        let mut agreement: Agreement =
            env.storage().instance().get(&Symbol::new(&env, "agreement")).unwrap();

        agreement.client.require_auth();

        if agreement.funded && agreement.completed {

            let token_client = token::Client::new(&env, &token_address);

            token_client.transfer(
                &env.current_contract_address(),
                &agreement.worker,
                &agreement.amount,
            );
        }
    }
}
