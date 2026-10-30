"""AI agents. They advise; deterministic code moves money.

Every agent has a rule-based fallback, so the system behaves sensibly with no model configured
or when the model is down or returns something invalid. Model output is always parsed into a
strict schema and checked before it is used.
"""
