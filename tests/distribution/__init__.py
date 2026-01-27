"""
Distribution Layer Tests

Priority tests for silent failure prevention:
1. URL Correctness - PUBLIC_* base URLs render correctly
2. GET Safety - /execute GET does NOT mutate state
3. Token Single-Use - Token consumption is enforced
4. Outbox Idempotency - Same digest_date doesn't create duplicates
5. Anti-Spam Selection - Already-digested companies excluded
6. Event-Type Isolation - Notion worker doesn't claim email_digest
"""
