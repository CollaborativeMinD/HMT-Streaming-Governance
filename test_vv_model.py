"""Execute the model's Boolean calculations with a restricted expression interpreter."""
import ast
import csv
import itertools
import re
import unittest

from vv_common import ROOT, catalog


def calculation(name):
    source = (ROOT / 'governance.sysml').read_text()
    body = re.search(r'calc def ' + name + r' \{(.*?)\n    \}', source, re.S)[1]
    return ast.parse('(' + re.search(r'return : Boolean = (.*?);', body, re.S)[1].strip() + ')', mode='eval').body


def interpret(node, values):
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.BoolOp):
        results = [interpret(child, values) for child in node.values]
        return all(results) if isinstance(node.op, ast.And) else any(results)
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = interpret(node.left, values), interpret(node.comparators[0], values)
        if isinstance(node.ops[0], ast.Eq):
            return left == right
        if isinstance(node.ops[0], ast.GtE):
            return left >= right
    raise ValueError('Unsupported model expression: ' + ast.dump(node))


class FunctionalModelTests(unittest.TestCase):
    def test_sysml_requirements_match_sql_catalog(self):
        model = (ROOT / 'governance.sysml').read_text()
        self.assertEqual(set(re.findall(r"requirement <'([^']+)'>", model)), {r['id'] for r in catalog()['requirements']})

    def test_model_boolean_calculations_and_operational_gate(self):
        trust = calculation('TrustDecision')
        available = calculation('VerificationAvailable')
        rows = []
        keys = ['sequenceValid', 'predecessorValid', 'payloadValid', 'sessionActive',
                'engineOpen', 'workerAlive', 'engineUnheld', 'withinCapacity']
        for bits in itertools.product((False, True), repeat=8):
            values = dict(zip(keys, bits))
            integrity = interpret(trust, values)
            operational = interpret(available, values)
            self.assertEqual(integrity, all(bits[:4]))
            self.assertEqual(operational, all(bits[4:]))
            outcome = 'HOLD' if not operational else 'DELIVER' if integrity else 'QUARANTINE'
            self.assertEqual(outcome == 'DELIVER', all(bits))
            rows.append(dict(values, permitted=integrity, operational=operational, disposition=outcome))
        with (ROOT / 'vv_evidence/model_truth_table.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_model_boundary_calculation_edges(self):
        expression = calculation('BoundaryEligible')
        for size, minimum, maximum, remainder, target in itertools.product(
                (0, 1, 63, 64, 127, 128, 129), (1, 64), (64, 128), (0, 1, 2), (0, 1)):
            if minimum > maximum:
                continue
            values = dict(chunkBytes=size, minimumBytes=minimum, maximumBytes=maximum, remainder=remainder, target=target)
            expected = size >= minimum and (remainder == target or size >= maximum)
            self.assertEqual(interpret(expression, values), expected)
