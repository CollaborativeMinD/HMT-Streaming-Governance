-- Materialize EVERY tuple; do not pre-filter the Cartesian product.
INSERT INTO cross_findings(invariant_id,failure_id,gate_id,obligation_id,scope_reason)
SELECT i.id,f.id,g.id,o.id,
 CASE WHEN o.id IS NOT NULL THEN o.rationale
 WHEN NOT EXISTS(SELECT 1 FROM obligations t WHERE t.invariant_id=i.id AND t.failure_id=f.id)
 THEN 'No declared threat relation for '||f.id||' against '||i.id||'; finite catalog scope, not proof of immunity.'
 ELSE g.id||' does not own this invariant/challenge pair; see its obligation at the declared owner gate.' END
FROM invariants i CROSS JOIN failure_modes f CROSS JOIN gates g
LEFT JOIN obligations o ON o.invariant_id=i.id AND o.failure_id=f.id AND o.gate_id=g.id;
