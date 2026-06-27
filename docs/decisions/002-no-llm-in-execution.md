# ADR 002 — No LLM in the Live Execution Path

**Date:** 2026-06-26
**Status:** Accepted

## Decision
The LLM (Claude) plays the role of strategist and designer only. Deterministic Python code executes all trades in production. No AI API calls are made at trade time.

## Reasoning
- **Latency:** LLM API calls add unpredictable delay to time-sensitive trade decisions
- **Cost:** Calling an LLM every hour or on every signal adds ongoing API costs with no clear benefit
- **Reliability:** An external API dependency in the execution path is a point of failure — the system should be able to trade even if the LLM is unavailable
- **Auditability:** Deterministic rules are inspectable and debuggable. LLM decisions are harder to audit and reproduce
- **Trust:** A system that can explain exactly why it placed a trade is safer than one that defers to a model at runtime

## How the LLM is Used
1. Design and tune strategy rules, entry/exit logic, and risk parameters
2. Interpret backtest results and suggest adjustments
3. Help write and review the deterministic code that runs in production
4. Post-trade analysis and strategy evolution (never real-time)

## Consequences
- All strategy logic must be expressible as deterministic code before going live
- The LLM's value is front-loaded into the design and tuning phases
- An LLM review/override layer could be added later as a separate non-blocking layer, once the core system is proven
