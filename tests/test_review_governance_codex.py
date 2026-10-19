"""Created by Codex: publication, source provenance, decisions and impact regressions."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import review_governance as governance


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'translations/T1579-001-baihua.md'
        self.path.parent.mkdir()
        self.path.write_text('# Test\n\n## 01 First\nRange: T30n1579_p0001a01\n\nSource:\n<<<\n無想定。\n>>>\n\nTranslation:\n<<<\n無想定。\n>>>\n\n## 02 Second\nRange: T30n1579_p0001a02\n\nSource:\n<<<\n作意。\n>>>\n\nTranslation:\n<<<\n作意。\n>>>\n')
        self.data = self.root / 'data/T1579-001.json'
        governance.write_json(self.data, {'version': 'fixture-1', 'results': ['<span class="t" l="0001a01">無想定。</span><span class="t" l="0001a02">作意。</span>']})
        self.term = {'id': 'nonperception', 'xuanzang': '無想定', 'source_terms': ['無想定'], 'review_required': True}
        governance.write_json(self.root / governance.GLOSSARY, {'terms': [self.term]})
        snapshot = self.root / 'translations/review/sources/fixture.json'
        governance.write_json(snapshot, {'source_id': 'fixture', 'lines': {'L1': '無想定的註釋。'}})
        self.registry = {'schema_version': 1,
                         'sources': [{'id': 'fixture', 'snapshot': snapshot.relative_to(self.root).as_posix(), 'sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(), 'work': 'fixture', 'author': 'fixture editor', 'edition': 'fixture-1', 'url': 'https://example.org/fixture'}],
                         'evidence': [{'id': 'quote', 'source_id': 'fixture', 'locators': ['L1'], 'quote': '無想定', 'relation': 'direct_commentary', 'term_ids': ['nonperception']}],
                         'issues': [{'id': 'issue', 'term_id': 'nonperception', 'risk': 'high', 'status': 'open', 'interpretations': [{'id': 'keep', 'label': 'Keep', 'evidence_ids': ['quote']}]}]}
        self.save()

    def save(self):
        governance.write_json(self.root / governance.REGISTRY, self.registry)

    def approve(self):
        governance.decide(self.root, 'issue', 'keep', 'fixture editor', 'Fixture evidence supports this reading.')
        state = governance.collect_state(self.root)
        return {key: {'verdict': 'pass', 'fingerprint': governance.review_fingerprint(item)} for key, item in state['occurrences'].items()}

    def test_unresolved_high_risk_blocks_but_low_risk_can_pass(self):
        self.assertTrue(governance.publication_issues(self.root, self.path))
        self.term['review_required'] = False
        governance.write_json(self.root / governance.GLOSSARY, {'terms': [self.term]})
        self.registry['issues'] = []
        self.save()
        self.assertEqual([], governance.publication_issues(self.root, self.path))

    def test_approved_evidence_still_requires_parallel_review(self):
        reviews = self.approve()
        self.assertEqual([], governance.publication_issues(self.root, self.path, reviews))
        next(iter(reviews.values()))['verdict'] = 'not_checked'
        self.assertTrue(governance.publication_issues(self.root, self.path, reviews))

    def test_fake_locator_quote_and_changed_snapshot_are_rejected(self):
        for field, value in [('locators', ['missing']), ('quote', 'invented')]:
            registry = copy.deepcopy(self.registry)
            registry['evidence'][0][field] = value
            self.assertTrue(governance.verify_evidence(self.root, registry, 'quote'))
        path = self.root / self.registry['sources'][0]['snapshot']
        path.write_text('{}')
        self.assertTrue(governance.verify_evidence(self.root, self.registry, 'quote'))

    def test_one_source_character_invalidates_only_the_affected_clause(self):
        reviews = self.approve()
        before = governance.collect_state(self.root)
        payload = governance.read_json(self.data)
        payload['results'][0] = payload['results'][0].replace('無想定。', '無想定也。')
        governance.write_json(self.data, payload)
        after = governance.collect_state(self.root)
        difference = governance.impact(before, after)
        self.assertEqual(['T1579-001:T30n1579_p0001a01'], difference['affected_clauses'])
        self.assertTrue(governance.publication_issues(self.root, self.path, reviews))
        self.assertEqual(before['clauses']['T1579-001:T30n1579_p0001a02'], after['clauses']['T1579-001:T30n1579_p0001a02'])

    def test_terminology_or_decision_changes_expire_the_parallel_review(self):
        for kind in ('term', 'decision'):
            with self.subTest(kind=kind):
                reviews = self.approve()
                before = governance.collect_state(self.root)
                if kind == 'term':
                    glossary = governance.read_json(self.root / governance.GLOSSARY)
                    glossary['terms'][0]['plain'] = 'Changed interpretation'
                    governance.write_json(self.root / governance.GLOSSARY, glossary)
                else:
                    registry = governance.load_registry(self.root)
                    registry['issues'][0]['decision']['rationale'] = 'Changed reasoning'
                    governance.write_json(self.root / governance.REGISTRY, registry)
                after = governance.collect_state(self.root)
                self.assertEqual(['T1579-001:T30n1579_p0001a01'], governance.impact(before, after)['affected_clauses'])
                self.assertTrue(governance.publication_issues(self.root, self.path, reviews))

    def test_translation_edit_expires_approval(self):
        reviews = self.approve()
        self.path.write_text(self.path.read_text().replace('Translation:\n<<<\n無想定。', 'Translation:\n<<<\n別種定。'))
        self.assertTrue(governance.publication_issues(self.root, self.path, reviews))

    def audited_claims(self):
        import audit
        import build_translation_html as bth
        self.approve()
        entry = bth.parse_entries(self.path.read_text())[0]
        context = governance.entry_context(self.root, self.path, entry)
        store = audit.AuditStore(self.root, 'governance-fixture')
        unit = 'T1579-001-001'
        response = {'stage': 'review_parallel', 'unit_id': unit,
                    'source_hash': audit.sha256_text(entry.source), 'verdict': 'pass', 'findings': []}
        ref = store.artifact('response', json.dumps(response))
        event = store.append({'stage': 'review_parallel', 'unit_id': unit, 'status': 'ok',
                              'model': 'fixture-model', 'effort': 'high',
                              'hashes': {'governance_sha256': context['sha256'], 'source_sha256': response['source_hash']},
                              'artifacts': {'response': ref}})
        claims = {unit: {'review_parallel': {'verdict': 'pass', 'model': 'fixture-model', 'effort': 'high',
                  'governance_bindings': context['bindings'], 'review_event_id': event['event_id'],
                  'source_sha256': response['source_hash'], 'source_quote': entry.source, 'translation_quote': entry.translation}}}
        return store, claims

    def test_audited_pass_is_required_and_response_tampering_blocks(self):
        store, claims = self.audited_claims()
        self.assertEqual([], governance.gate_issues(self.root, self.path, claims, store.events()))
        self.assertTrue(governance.gate_issues(self.root, self.path, claims, []))
        event = store.events()[0]
        (self.root / event['artifacts']['response']['path']).write_bytes(b'forged')
        self.assertTrue(governance.gate_issues(self.root, self.path, claims, store.events()))

    def test_publisher_rejects_missing_claims_and_live_decision_change(self):
        import publisher
        store, claims = self.audited_claims()
        claims_ref = store.artifact('governance_claims', json.dumps(claims))
        events_ref = store.artifact('audit_event_log', store.event_path.read_bytes())
        seal = self.root / 'seal.json'
        publisher.create_attestation(self.root, seal, {'translation': self.path})
        with self.assertRaises(publisher.PublishError):
            publisher.verify_attestation(self.root, seal)
        seal.unlink()
        publisher.create_attestation(self.root, seal, {'translation': self.path,
            'governance_claims': self.root / claims_ref['path'],
            'audit_event_log_snapshot': self.root / events_ref['path']})
        publisher.verify_attestation(self.root, seal)
        registry = governance.load_registry(self.root)
        registry['issues'][0]['decision']['rationale'] = 'Changed after seal'
        governance.write_json(self.root / governance.REGISTRY, registry)
        with self.assertRaises(publisher.PublishError):
            publisher.verify_attestation(self.root, seal)

    def test_targeted_invalidation_retains_other_unit_and_reports_stale(self):
        self.approve()
        before = governance.collect_state(self.root)
        self.term['plain'] = 'New wording'
        governance.write_json(self.root / governance.GLOSSARY, {'terms': [self.term]})
        after = governance.collect_state(self.root)
        claims = {'first': {'pass': True}, 'second': {'pass': True}}
        units = {'first': 'T1579-001:T30n1579_p0001a01', 'second': 'T1579-001:T30n1579_p0001a02'}
        result = governance.invalidate_reviews(before, after, claims, units)
        self.assertEqual(['T1579-001:T30n1579_p0001a01'], result['affected_clauses'])
        self.assertEqual({'second': {'pass': True}}, claims)
        self.assertEqual('stale', governance.issue_list(self.root)[0]['effective_status'])

    def test_candidate_change_reopens_and_preserves_decision_history(self):
        self.approve()
        governance.update_issue(self.root, 'issue', {'id': 'other', 'label': 'Other reading', 'evidence_ids': ['quote']})
        issue = governance.issue_list(self.root)[0]
        self.assertEqual('open', issue['effective_status'])
        self.assertEqual(1, len(issue['decision_history']))
        self.assertIsNone(issue['decision'])
        with self.assertRaises(ValueError):
            governance.update_issue(self.root, 'issue', {'id': 'bad', 'label': 'No source', 'evidence_ids': ['missing']})

    def test_version_change_invalidates_all_occurrences_even_if_text_unchanged(self):
        reviews = self.approve()
        before = governance.collect_state(self.root)
        data = governance.read_json(self.data)
        data['version'] = 'fixture-2'
        governance.write_json(self.data, data)
        self.assertEqual(2, len(governance.impact(before, governance.collect_state(self.root))['affected_clauses']))
        self.assertTrue(governance.publication_issues(self.root, self.path, reviews))

    def test_reader_shows_candidates_relations_and_catalog_limits(self):
        context = governance.render_context(ROOT, 'manaskara')
        self.assertIn('普光', context)
        self.assertIn('法寶', context)
        self.assertIn('眾賢', context)
        self.assertIn('窺基', context)
        self.assertIn('神泰', context)
        self.assertIn('僅書目／待補定位引文', context)
        issue = governance.render_context(self.root, 'nonperception')
        self.assertIn('Keep', issue)
        self.assertIn('T1579-001-baihua.html#T30n1579_p0001a01', issue)


    def test_runner_reuses_only_unaffected_audited_sections(self):
        import audit
        import runner
        import build_translation_html as bth
        entries = bth.parse_entries(self.path.read_text())
        job = {'id': 'cache-test', 'work': 'T1579', 'model': 'auto'}
        prog = {'clause_contracts': {}}
        for idx, entry in enumerate(entries):
            clause = {'clause_id': entry.range_label, 'literal': entry.source,
                      'vernacular': entry.translation, 'speakers': None,
                      **{key: [] for key in runner.TRANSLATION_NESTED_SHAPES}}
            prog['clause_contracts'][runner.unit_id(job, 1, idx)] = {
                'stage': 'translate', 'source_hash': runner.source_hash(entry.source), 'clause': clause}
        store = audit.AuditStore(self.root, job['id'])
        def review(job, prompt, stage, **kwargs):
            context = kwargs['context']
            response = {'stage': stage, 'unit_id': context['unit_id'], 'source_hash': context['source_hash'],
                        'verdict': 'pass', 'findings': []}
            event = store.append({'stage': stage, 'status': 'ok', 'unit_id': context['unit_id'],
                                  'model': runner.stage_model(job, stage), 'effort': kwargs['effort'],
                                  'hashes': {'governance_sha256': context['governance_sha256'],
                                             'source_sha256': context['source_hash']},
                                  'artifacts': {'response': store.artifact('response', json.dumps(response))}})
            return response, [], event['event_id']
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, 'save_job'), \
             patch.object(runner, 'GLOSSARY_PATH', self.root / governance.GLOSSARY), \
             patch.object(runner, 'semantic_review_call', side_effect=review) as call:
            runner.review_pass(job, 1, self.path, 'review_terms', prog, 1)
            self.assertEqual(2, call.call_count)
            runner.review_pass(job, 1, self.path, 'review_terms', prog, 2)
            self.assertEqual(2, call.call_count)
            self.term['plain'] = 'Changed interpretation'
            governance.write_json(self.root / governance.GLOSSARY, {'terms': [self.term]})
            runner.review_pass(job, 1, self.path, 'review_terms', prog, 3)
            self.assertEqual(3, call.call_count)
            self.assertEqual('T1579-001-001', call.call_args.kwargs['context']['unit_id'])

    def test_real_seed_quotes_match_their_snapshots(self):
        registry = governance.load_registry(ROOT)
        self.assertGreaterEqual(len(registry['evidence']), 5)
        for evidence in registry['evidence']:
            self.assertEqual([], governance.verify_evidence(ROOT, registry, evidence['id']))


if __name__ == '__main__':
    unittest.main()
