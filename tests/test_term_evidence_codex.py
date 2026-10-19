"""Codex contribution: regression coverage for context and language evidence."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_translation_terms as terms
import build_translation_html as builder
import runner


class TermEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.glossary = json.loads((ROOT / 'translations/glossary/T1579-terms.json').read_text())

    def check_text(self, source, translation):
        return terms.check_translation_terms(self.glossary, [terms.Entry('Example', source, translation)])

    def test_attachment_is_not_a_gustatory_object(self):
        self.assertEqual([], self.check_text('味定、愛\n味、一味平等', '味定、愛味、一味平等'))

    def test_mixed_senses_still_require_the_taste_object(self):
        self.assertTrue(any('味境' in issue for issue in self.check_text('愛味。舌甞味。', '愛味。')))

    def test_gustatory_object_passes_with_its_real_gloss(self):
        self.assertEqual([], self.check_text('聲、香、味、觸', '聲、香、味境、觸'))

    def test_wind_is_not_a_truth_aspect(self):
        self.assertEqual([], self.check_text('空行風', '空中流動的風'))
        self.assertTrue(self.check_text('十六行中的空行', '空中流動的風'))

    def test_pure_abode_is_not_the_lake(self):
        self.assertEqual([], self.check_text('無煩、無熱、善現', '無煩、無熱、善現'))
        self.assertTrue(self.check_text('無熱池', '某處'))

    def test_runner_uses_the_same_context_selection(self):
        selected = json.loads(runner.glossary_subset_json(self.glossary, '愛味'))
        self.assertNotIn('taste-object', [term['id'] for term in selected])

    def test_all_existing_language_candidates_remain_unresolved(self):
        self.assertEqual([], terms.validate_glossary(self.glossary))
        for term in self.glossary['terms']:
            for language in ('chinese', 'sanskrit', 'pali'):
                records = term['language_evidence'][language]
                self.assertTrue(all(record['status'] == 'unresolved' for record in records))

    def test_attested_requires_a_source_and_review(self):
        glossary = copy.deepcopy(self.glossary)
        record = glossary['terms'][0]['language_evidence']['sanskrit'][0]
        record['status'] = 'attested'
        self.assertTrue(terms.validate_glossary(glossary))
        record['evidence'] = [{'source_id': 'edition-1', 'locator': 'p. 12',
                               'quote': 'manaskara', 'url': 'https://example.org/text'}]
        record['reviewed_by'] = 'editor'
        record['reviewed_at'] = '2026-10-19'
        self.assertEqual([], terms.validate_glossary(glossary))

    def test_unsafe_link_and_mismatched_candidate_are_rejected(self):
        for field, value in [('form', 'invented'), ('status', 'stable')]:
            with self.subTest(field=field):
                glossary = copy.deepcopy(self.glossary)
                glossary['terms'][0]['language_evidence']['sanskrit'][0][field] = value
                self.assertTrue(terms.validate_glossary(glossary))
        glossary = copy.deepcopy(self.glossary)
        record = glossary['terms'][0]['language_evidence']['sanskrit'][0]
        record['evidence'] = [{'source_id': 'x', 'locator': 'p1', 'quote': 'x', 'url': 'javascript:alert(1)'}]
        self.assertTrue(terms.validate_glossary(glossary))

    def test_cards_separate_languages_and_escape_evidence(self):
        markup = builder.render_term_cards(self.glossary)
        self.assertIn('巴利語', markup)
        self.assertIn('梵語', markup)
        self.assertIn('待考證', markup)
        self.assertIn('無來源證據', markup)
        glossary = copy.deepcopy(self.glossary)
        glossary['terms'][0]['language_evidence']['sanskrit'][0]['note'] = '<script>bad()</script>'
        self.assertNotIn('<script>bad()</script>', builder.render_term_cards(glossary))

# Codex contribution: handoff metadata must survive runner status publication.
class StatusArtifactTests(unittest.TestCase):
    def test_job_status_does_not_replace_handoff_status(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobs = root / 'jobs'
            jobs.mkdir()
            handoff = root / 'status.json'
            handoff.write_text('{"schema":1}')
            runtime = root / 'jobs-status.json'
            with patch.object(runner, 'JOBS_DIR', jobs), patch.object(runner, 'STATUS_JSON', runtime):
                runner.publish_status()
            self.assertEqual({'schema': 1}, json.loads(handoff.read_text()))
            self.assertEqual([], json.loads(runtime.read_text())['jobs'])
        self.assertEqual('jobs-status.json', runner.STATUS_JSON.name)
        self.assertIn('fetch("jobs-status.json")', (ROOT / 'docs/status.html').read_text())

class EvidenceInputTests(unittest.TestCase):
    def test_malformed_status_returns_an_issue_instead_of_crashing(self):
        term = {'id': 'x', 'xuanzang': 'X', 'sanskrit': [], 'pali': [],
                'language_evidence': {'chinese': [{'form': 'X', 'status': [], 'evidence': []}],
                                      'sanskrit': [], 'pali': []}}
        self.assertTrue(terms.validate_language_evidence(term))

    def test_model_proposal_cannot_self_attest(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'glossary.json'
            glossary = json.loads((ROOT / 'translations/glossary/T1579-terms.json').read_text())
            proposed = copy.deepcopy(glossary['terms'][0])
            proposed['id'] = 'new-candidate'
            proposed['language_evidence']['sanskrit'][0]['status'] = 'attested'
            path.write_text(json.dumps(glossary))
            with patch.object(runner, 'GLOSSARY_PATH', path), patch.object(runner, 'LOCKS_DIR', Path(folder)), patch.object(runner, 'log'):
                runner.append_new_terms(json.dumps([proposed]), {})
            result = json.loads(path.read_text())['terms'][-1]
            self.assertEqual('new-candidate', result['id'])
            self.assertEqual('unresolved', result['language_evidence']['sanskrit'][0]['status'])
            self.assertEqual([], result['language_evidence']['sanskrit'][0]['evidence'])


if __name__ == '__main__':
    unittest.main()
