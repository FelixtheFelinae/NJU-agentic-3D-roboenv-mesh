"""Shared authoring policy; priors are permitted without direct observation."""

PRIOR_INSTRUCTION = """Generate a plausible functional structure from appearance and mechanical
common knowledge. Direct observation, retrieval and motion video are NOT prerequisites
for hidden structure. For a closed drawer, you may choose side-mounted sliding guides,
their approximate placement, and a prismatic joint without seeing rails or drawer motion.
Choose a useful likely candidate; alternatives need only be recorded, not all built.
Declare mechanical knowledge as evidence origin=prior with prior: IDs and a rationale.
Mark hidden interfaces/mates as inferred or assumed, joints as inferred/assumed, and
guessed dimensions, mass, placement and travel as assumed, referencing that prior.
These references document model assumptions; they are NOT photographs or measurements.
Do not request new views merely to justify a prior. Preserve observed exterior facts
and explicit user requirements. Keep observed_motion empty when no motion was observed.
Approximate rails may share a rigid body with the cabinet/drawer; an ideal prismatic
joint is a kinematic approximation, not verified rail contact, friction or load capacity.
Unknown parameters may stay unknown, or be explicitly assumed for a build candidate.
All geometry and physics checks still apply; do not claim this candidate is the actual
hidden mechanism or experimentally verified."""


def prior_refs(blueprint):
    return {e.id for e in getattr(blueprint, "evidence", ()) if e.origin == "prior"}
