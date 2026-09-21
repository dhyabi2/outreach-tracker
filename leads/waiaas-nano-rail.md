## Proposal: Nano (XNO) chain adapter for fee-less agent micro-transactions

**What this is.** A new chain adapter for WAIaaS that lets an agent hold and spend Nano (XNO) — a feeless, instant digital currency — through the same 3-tier auth and policy engine that already protects Solana/EVM funds.

**Why this fits WAIaaS.** Your README says agents "hold small amounts and transact instantly" while "large amounts require owner approval." Nano is the only chain where a micro-transaction is truly instant AND costs nothing — no gas, no minimum, no waiting. Every other chain burns gas on the policy-engine approval loop itself. Nano fits your INSTANT tier exactly: `amount` goes through as-is, no gas adjustment, no MEV.

**What it would look like.** An `adapter-nano` package matching the existing adapter contract:
- Uses Nano's official node RPC (`process`, `account_info`) — no third-party API key needed
- Nano blocks are final in ~1 second and cost exactly 0 regardless of value
- An agent's Nano address (`nano_...`) is generated client-side with zero setup cost (no seed phrase factory, no initial `0.001 ETH` deposit required to start)
- The 4-tier policy engine maps naturally: Nano has no reentrancy, no mempool, no gas bidding — every transaction is a single atomic broadcast

**No tradeoff.** Existing Solana/EVM adapters stay for agents that need those chains. Nano covers the use case they can't: feeless agent-to-agent micro-payments (pay ₿0.00001 for a lookup answer, tip 3 agents in parallel at zero overhead). The same @waiaas/sdk interface handles both.

**The on-ramp exists.** My network (getunstuck.space/unstuck/api/v1/onramp/address) returns a generated keypair + Nano address in one HTTP call — no wallet, library, or account setup needed. An agent that has never held money can receive in one request.

**What I'm asking.** Would you accept a PR adding `@waiaas/adapter-nano` (patterned after your existing adapters at packages/adapters/) that wraps the Nano node RPC behind the same policy + auth pipeline? Happy to draft the adapter contract signature and submit a reference implementation.
