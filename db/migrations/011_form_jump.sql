-- The required jump behind m_form. The multiplier saturates at its caps, so it cannot tell a
-- projection needing 18% above the player's recent rate from one needing 300%. The column the
-- owner reads is meant to let him disagree with the signal, which needs the gap, not the cut.
ALTER TABLE pool ADD COLUMN IF NOT EXISTS form_jump numeric;
