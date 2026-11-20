"""Rule sets a user teaches from a few documents of one format.

E2's rules are general; a rule set is specific to one format (one supplier's
invoices) and learned from the user's own examples, with no model and no cost
(learn.py). Documents are then read with it locally (read.py); its gate sends
the documents it cannot read to E5. Saved by name in templates/ (store.py).
"""
