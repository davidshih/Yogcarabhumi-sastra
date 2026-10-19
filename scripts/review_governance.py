#!/usr/bin/env python3
"""Created by Codex: evidence provenance, term decisions, and targeted review invalidation."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path

from check_translation_terms import source_term_matches
from make_translation_skeleton import extract_lines, line_key
from check_translation_coverage import normalize_source_text

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path('translations/review/registry-codex.json')
GLOSSARY = Path('translations/glossary/T1579-terms.json')


def digest(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Source snapshot must stay inside the repository')
    return path


def load_registry(root: Path) -> dict:
    path = root / REGISTRY
    if not path.exists():
        return {'schema_version': 1, 'sources': [], 'evidence': [], 'issues': []}
    registry = read_json(path)
    if registry.get('schema_version') != 1:
        raise ValueError('Unsupported review registry schema')
    for field in ('sources', 'evidence', 'issues'):
        values = registry.get(field)
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ValueError(f'{field} must be an array of objects')
        ids = [item.get('id') for item in values]
        if any(not isinstance(key, str) or not key for key in ids) or len(ids) != len(set(ids)):
            raise ValueError(f'{field} needs unique nonempty IDs')
    term_ids = [item.get('term_id') for item in registry['issues']]
    if len(term_ids) != len(set(term_ids)) or any(not isinstance(key, str) or not key for key in term_ids):
        raise ValueError('Issues require unique nonempty term IDs')
    for issue in registry['issues']:
        if issue.get('status') not in ('open', 'resolved') or issue.get('risk') not in ('high', 'normal'):
            raise ValueError('Invalid issue status or risk')
        choices = issue.get('interpretations', [])
        ids = [item.get('id') for item in choices]
        if len(ids) != len(set(ids)) or any(not key for key in ids):
            raise ValueError('Candidate IDs must be unique')
    return registry


def verify_evidence(root: Path, registry: dict, evidence_id: str) -> list[str]:
    evidence = next((item for item in registry['evidence'] if item['id'] == evidence_id), None)
    if evidence is None:
        return [f'{evidence_id}: unknown evidence']
    source = next((item for item in registry['sources'] if item['id'] == evidence.get('source_id')), None)
    if source is None:
        return [f'{evidence_id}: unknown source']
    try:
        path = safe_path(root, source['snapshot'])
        raw = path.read_bytes()
        snapshot = json.loads(raw)
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            return [f'{evidence_id}: source snapshot changed']
        if snapshot.get('source_id') != source['id']:
            return [f'{evidence_id}: snapshot identity mismatch']
        if not all(source.get(key) for key in ('work', 'author', 'edition', 'url')):
            return [f'{evidence_id}: incomplete bibliographic provenance']
        if urlsplit(source['url']).scheme not in ('http', 'https') or not urlsplit(source['url']).netloc:
            return [f'{evidence_id}: invalid source URL']
        lines = snapshot['lines']
        locators = evidence['locators']
        if not isinstance(locators, list) or not locators or any(key not in lines for key in locators):
            return [f'{evidence_id}: invalid locator']
        if len(set(locators)) != len(locators) or locators != sorted(locators):
            return [f'{evidence_id}: locators must be unique and ordered']
        text = ''.join(lines[key] for key in locators)
        if not isinstance(evidence.get('quote'), str) or not evidence['quote'] or evidence['quote'] not in text:
            return [f'{evidence_id}: quote does not match the located source']
        if evidence.get('relation') not in ('direct_commentary', 'cross_text', 'quotation', 'disagreement'):
            return [f'{evidence_id}: invalid commentary relation']
        if evidence['relation'] == 'quotation' and not evidence.get('quoted_author'):
            return [f'{evidence_id}: quoted author is required']
    except (OSError, KeyError, TypeError, ValueError) as error:
        return [f'{evidence_id}: invalid source evidence: {error}']
    return []


def evidence_fingerprint(root: Path, registry: dict, ids: list[str]) -> str:
    records = []
    for key in sorted(ids):
        evidence = next((item for item in registry['evidence'] if item['id'] == key), {'id': key})
        source = next((item for item in registry['sources'] if item['id'] == evidence.get('source_id')), {})
        try:
            actual = hashlib.sha256(safe_path(root, source['snapshot']).read_bytes()).hexdigest()
        except (KeyError, OSError, ValueError):
            actual = None
        records.append({'evidence': evidence, 'source': source, 'actual_sha256': actual})
    return digest(records)


def collect_state(root: Path, paths: list[Path] | None = None) -> dict:
    # Import lazily: the reader also uses this module to render source evidence.
    from build_translation_html import parse_entries, parse_range
    glossary_path = root / GLOSSARY
    if not glossary_path.exists():
        return {'schema_version': 1, 'sources': {}, 'clauses': {}, 'occurrences': {}}
    terms = read_json(glossary_path)['terms']
    registry = load_registry(root)
    paths = paths if paths is not None else sorted((root / 'translations').glob('*-baihua.md'))
    clauses, occurrences, sources = {}, {}, {}
    for path in paths:
        name = path.stem.removesuffix('-baihua')
        if not path.resolve().is_relative_to(root.resolve()):
            continue
        data_path = root / 'data' / f'{name}.json'
        try:
            lines = extract_lines(data_path) if data_path.exists() else {}
        except (KeyError, IndexError, TypeError):
            lines = {}  # Missing source stays unavailable; high-risk publication fails closed.
        if data_path.exists():
            payload = read_json(data_path)
            sources[name] = {'path': str(data_path.relative_to(root)),
                             'sha256': hashlib.sha256(data_path.read_bytes()).hexdigest(),
                             'edition': payload.get('version', payload.get('edition', 'not recorded'))}
        for entry in parse_entries(path.read_text(encoding='utf-8')):
            start, end = parse_range(entry.range_label)
            clause_id = f'{name}:{start}'
            selected = [key for key in sorted(lines, key=line_key) if line_key(start) <= line_key(key) <= line_key(end)]
            live_source = ''.join(lines[key] for key in selected) if selected else entry.source
            matches = [term for term in terms if source_term_matches(term, live_source)]
            normalize = normalize_source_text
            source_mismatch = bool(lines) and normalize(live_source) != normalize(entry.source)
            clause = {'id': clause_id, 'file': path.relative_to(root).as_posix(), 'range': entry.range_label,
                      'title': entry.title, 'source_edition': sources.get(name, {}).get('edition'), 'source_sha256': digest(normalize(live_source)), 'source_available': bool(selected),
                      'translation_sha256': digest(entry.translation), 'source_mismatch': source_mismatch,
                      'source_lines': {key: digest(lines[key]) for key in selected},
                      'term_ids': sorted(term['id'] for term in matches)}
            clause['fingerprint'] = digest(clause)
            clauses[clause_id] = clause
            for term in matches:
                key = f'{clause_id}:{term["id"]}'
                related = [item for item in registry['evidence'] if term['id'] in item.get('term_ids', [])]
                issue = next((item for item in registry['issues'] if item['term_id'] == term['id']), None)
                candidate_ids = sorted({e for candidate in (issue or {}).get('interpretations', [])
                                        for e in candidate.get('evidence_ids', [])})
                evidence_ids = sorted({item['id'] for item in related} | set(candidate_ids))
                record = {'id': key, 'clause_id': clause_id, 'term_id': term['id'],
                          'file': clause['file'], 'range': entry.range_label,
                          'source_sha256': clause['source_sha256'], 'source_edition': clause['source_edition'],
                          'translation_sha256': clause['translation_sha256'],
                          'term_sha256': digest(term), 'evidence_ids': evidence_ids,
                          'evidence_sha256': evidence_fingerprint(root, registry, evidence_ids),
                          'required': bool(term.get('review_required')) or bool(issue and issue.get('risk') == 'high')}
                # Bind the alternatives and risk, but not the approval that signs this fingerprint.
                record['issue_sha256'] = digest({k: v for k, v in (issue or {}).items() if k not in ('decision', 'status', 'updated_at', 'decision_history')})
                record['fingerprint'] = digest(record)
                record['decision_sha256'] = digest({'status': (issue or {}).get('status'), 'decision': {k: v for k, v in ((issue or {}).get('decision') or {}).items() if k != 'bindings'}})
                occurrences[key] = record
    return {'schema_version': 1, 'sources': sources, 'clauses': clauses, 'occurrences': occurrences}


def impact(previous: dict, current: dict) -> dict:
    changes = []
    for kind in ('clauses', 'occurrences'):
        before, after = previous.get(kind, {}), current.get(kind, {})
        for key in sorted(set(before) | set(after)):
            old, new = before.get(key), after.get(key)
            if old == new:
                continue
            reasons = ['added'] if old is None else ['removed'] if new is None else [
                field for field in sorted(set(old) | set(new))
                if old.get(field) != new.get(field) and field != 'fingerprint']
            changes.append({'kind': kind, 'id': key, 'reasons': reasons,
                            'file': (new or old)['file'], 'clause_id': (new or old).get('clause_id', key)})
    return {'changes': changes, 'affected_clauses': sorted({item['clause_id'] for item in changes}),
            'source_artifacts_changed': sorted(key for key in set(previous.get('sources', {})) | set(current.get('sources', {}))
                                               if previous.get('sources', {}).get(key) != current.get('sources', {}).get(key))}


def decision_issues(root: Path, registry: dict, occurrence: dict) -> list[str]:
    issue = next((item for item in registry['issues'] if item['term_id'] == occurrence['term_id']), None)
    key = occurrence['id']
    if not issue or issue.get('status') != 'resolved':
        return [f'{key}: high-risk terminology issue is unresolved']
    decision = issue.get('decision') or {}
    if not all(isinstance(decision.get(field), str) and decision[field].strip()
               for field in ('choice_id', 'rationale', 'reviewer', 'reviewed_at')):
        return [f'{key}: incomplete editorial decision']
    choice = next((item for item in issue.get('interpretations', []) if item['id'] == decision['choice_id']), None)
    if not choice or not choice.get('evidence_ids'):
        return [f'{key}: accepted interpretation needs source evidence']
    errors = [error for source in choice['evidence_ids'] for error in verify_evidence(root, registry, source)]
    if decision.get('bindings', {}).get(key) != occurrence['fingerprint']:
        errors.append(f'{key}: editorial approval is stale or missing for this occurrence')
    return errors


def review_fingerprint(occurrence: dict) -> str:
    return digest([occurrence['fingerprint'], occurrence['decision_sha256']])


def publication_issues(root: Path, path: Path, reviews: dict | None = None) -> list[str]:
    state = collect_state(root, [path])
    registry = load_registry(root)
    errors = [f'{key}: translation source differs from downloaded source'
              for key, clause in state['clauses'].items() if clause['source_mismatch']]
    for occurrence in state['occurrences'].values():
        if not occurrence['required']:
            continue
        if not state['clauses'][occurrence['clause_id']]['source_available']:
            errors.append(f'{occurrence["id"]}: downloaded source is missing')
        errors += decision_issues(root, registry, occurrence)
        review = (reviews or {}).get(occurrence['id'], {})
        if review.get('verdict') != 'pass' or review.get('fingerprint') != review_fingerprint(occurrence):
            errors.append(f'{occurrence["id"]}: high-risk parallel review is missing, not_checked, or stale')
    return errors



def entry_context(root: Path, path: Path, entry, state: dict | None = None) -> dict:
    """Bind evidence and decisions before the review call starts."""
    from build_translation_html import parse_range
    state = state or collect_state(root, [path])
    key = f'{path.stem.removesuffix("-baihua")}:{parse_range(entry.range_label)[0]}'
    occurrences = {k: v for k, v in state['occurrences'].items() if v['clause_id'] == key}
    registry = load_registry(root)
    ids = {eid for value in occurrences.values() for eid in value['evidence_ids']}
    evidence = []
    for item in registry['evidence']:
        if item['id'] in ids and not verify_evidence(root, registry, item['id']):
            source = next(s for s in registry['sources'] if s['id'] == item['source_id'])
            evidence.append({**item, 'source_id': item['id'], 'bibliography': source})
    bindings = {k: review_fingerprint(v) for k, v in occurrences.items() if v['required']}
    return {'bindings': bindings, 'sha256': digest({'occurrences': occurrences, 'clause': state['clauses'].get(key)}), 'evidence': evidence,
            'decisions': [item for item in registry['issues'] if item['term_id'] in {v['term_id'] for v in occurrences.values()}]}


def verified_reviews(root: Path, path: Path, attestations: dict, events: list[dict]) -> dict:
    """Accept only review claims linked to successful immutable call artifacts."""
    import audit
    from build_translation_html import parse_entries
    reviews = {}
    by_id = {event['event_id']: event for event in events}
    state = collect_state(root, [path])
    for entry in parse_entries(path.read_text(encoding='utf-8')):
        context = entry_context(root, path, entry, state)
        for unit, stages in attestations.items():
            claim = stages.get('review_parallel', {})
            event = by_id.get(claim.get('review_event_id'), {})
            if not context['bindings'] or claim.get('governance_bindings') != context['bindings']:
                continue
            try:
                response = json.loads(audit.read_artifact(root, event['artifacts']['response']))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if (claim.get('verdict') == 'pass' and event.get('status') == 'ok'
                    and event.get('stage') == response.get('stage') == 'review_parallel'
                    and event.get('unit_id') == response.get('unit_id') == unit
                    and event.get('model') == claim.get('model') and bool(claim.get('model'))
                    and event.get('effort') == claim.get('effort')
                    and event.get('hashes', {}).get('governance_sha256') == context['sha256']
                    and event.get('hashes', {}).get('source_sha256') == claim.get('source_sha256') == response.get('source_hash') == audit.sha256_text(entry.source)
                    and claim.get('source_quote') == entry.source
                    and claim.get('translation_quote') == entry.translation
                    and response.get('verdict') == 'pass' and response.get('findings') == []):
                reviews.update({key: {'verdict': 'pass', 'fingerprint': value}
                                for key, value in context['bindings'].items()})
    return reviews


def gate_issues(root: Path, path: Path, attestations: dict, events: list[dict]) -> list[str]:
    if not (root / GLOSSARY).exists() or not path.resolve().is_relative_to(root.resolve()):
        return []
    return publication_issues(root, path, verified_reviews(root, path, attestations, events))


def invalidate_reviews(previous: dict, current: dict, attestations: dict, units: dict) -> dict:
    """Remove approvals for affected clauses and retain every other unit."""
    difference = impact(previous, current)
    affected = set(difference['affected_clauses'])
    for unit, clause in units.items():
        if clause in affected:
            attestations.pop(unit, None)
    return difference


def decide(root: Path, issue_id: str, choice_id: str, reviewer: str, rationale: str) -> dict:
    registry = load_registry(root)
    issue = next((item for item in registry['issues'] if item['id'] == issue_id), None)
    if not issue:
        raise ValueError('Unknown issue')
    choice = next((item for item in issue['interpretations'] if item['id'] == choice_id), None)
    if not choice or not choice.get('evidence_ids') or not reviewer.strip() or not rationale.strip():
        raise ValueError('A decision requires an evidenced choice, reviewer, and rationale')
    errors = [error for key in choice['evidence_ids'] for error in verify_evidence(root, registry, key)]
    if errors:
        raise ValueError('; '.join(errors))
    state = collect_state(root)
    if issue.get('decision'):
        issue.setdefault('decision_history', []).append(issue['decision'])
    issue['status'] = 'resolved'
    issue['decision'] = {'choice_id': choice_id, 'reviewer': reviewer, 'rationale': rationale,
                         'reviewed_at': datetime.now(timezone.utc).isoformat(),
                         'bindings': {key: item['fingerprint'] for key, item in state['occurrences'].items()
                                      if item['term_id'] == issue['term_id']}}
    write_json(root / REGISTRY, registry)
    return issue



def issue_list(root: Path) -> list[dict]:
    registry, state = load_registry(root), collect_state(root)
    result = []
    for item in registry['issues']:
        occurrences = [v for v in state['occurrences'].values() if v['term_id'] == item['term_id']]
        stale = [v['id'] for v in occurrences if decision_issues(root, registry, v)]
        result.append({**item, 'effective_status': 'stale' if item['status'] == 'resolved' and stale else item['status'],
                       'affected_occurrences': [v['id'] for v in occurrences], 'unapproved_occurrences': stale})
    return result


def update_issue(root: Path, issue_id: str, candidate: dict | None = None) -> dict:
    registry = load_registry(root)
    issue = next((i for i in registry['issues'] if i['id'] == issue_id), None)
    if issue is None:
        raise ValueError('Unknown issue')
    if candidate is not None:
        if not all(isinstance(candidate.get(k), str) and candidate[k].strip() for k in ('id', 'label')):
            raise ValueError('Candidate requires an ID and label')
        if not isinstance(candidate.get('evidence_ids'), list):
            raise ValueError('Candidate needs an evidence_ids array')
        errors = [e for key in candidate['evidence_ids'] for e in verify_evidence(root, registry, key)]
        if errors: raise ValueError('; '.join(errors))
        issue['interpretations'] = [i for i in issue['interpretations'] if i['id'] != candidate['id']] + [candidate]
    if issue.get('decision'):
        issue.setdefault('decision_history', []).append(issue['decision'])
    issue['decision'] = None
    issue['status'] = 'open'
    write_json(root / REGISTRY, registry)
    return issue


def render_context(root: Path, term_id: str, registry: dict | None = None, issues: list | None = None) -> str:
    """Reader cards distinguish authored comments, borrowed quotations, and catalog leads."""
    esc = lambda value: html.escape(str(value), quote=True)
    registry = registry if registry is not None else load_registry(root)
    issues = issues if issues is not None else issue_list(root)
    parts = ['<section><h3>註疏對照與疑義</h3><p>Codex contribution · 引文核對不等於義理裁決。</p>']
    relations = {'direct_commentary': '本論註釋', 'cross_text': '跨文本比較', 'quotation': '轉引', 'disagreement': '異說／評議'}
    for evidence in registry['evidence']:
        if term_id not in evidence.get('term_ids', []): continue
        source = next((v for v in registry['sources'] if v['id'] == evidence['source_id']), {})
        errors = verify_evidence(root, registry, evidence['id'])
        author = source.get('author', '未詳')
        parts.append('<details><summary>' + esc(author) + '《' + esc(source.get('title', '')) + '》 · '
                     + esc(relations.get(evidence['relation'], evidence['relation'])) + '</summary>')
        parts.append('<p>所引／所評作者：' + esc(evidence.get('quoted_author', '未標示轉引')) + '</p>')
        parts.append('<blockquote>' + esc(evidence['quote']) + '</blockquote><p>' + esc('、'.join(evidence['locators'])) + '</p>')
        parts.append('<p>' + esc(evidence.get('interpretation', '')) + '</p><p>採用說明：' + esc(evidence.get('adoption', '未裁定')) + '</p>')
        parts.append('<p>' + esc('; '.join(errors) if errors else '引文與本地版本核對通過；不自動證成梵巴對應。') + '</p>')
        url = source.get('url', '')
        if urlsplit(url).scheme in ('https', 'http') and urlsplit(url).netloc:
            parts.append('<a target="_blank" rel="noopener noreferrer" href="' + esc(url) + '">查看來源</a>')
        parts.append('</details>')
    for issue in issues:
        if issue['term_id'] != term_id: continue
        parts.append('<p><b>' + esc(issue.get('title', issue['id'])) + '</b> · ' + esc(issue['effective_status']) + '</p><ul>')
        for choice in issue['interpretations']:
            parts.append('<li>' + esc(choice['label']) + '；證據：' + esc(', '.join(choice['evidence_ids']) or '尚缺') + '</li>')
        parts.append('</ul><p>裁決：' + esc((issue.get('decision') or {}).get('rationale', '待人工考證；不以多數決採用。')) + '</p>')
        parts.append('<details><summary>受影響段落：' + str(len(issue['affected_occurrences'])) + '</summary><ul>')
        for occurrence in issue['affected_occurrences']:
            volume, locator, _ = occurrence.split(':', 2)
            parts.append('<li><a href="' + esc(volume + '-baihua.html#' + locator) + '">' + esc(volume + ' / ' + locator) + '</a></li>')
        parts.append('</ul></details>')
    parts.append('<details><summary>玄奘學團及後續註疏索引</summary><ul>')
    for source in [*registry['sources'], *registry.get('catalog', [])]:
        count = sum(e['source_id'] == source.get('id') for e in registry['evidence'])
        parts.append('<li>' + esc(source['author']) + '《' + esc(source.get('title', source['work'])) + '》 ' + esc(source['work'])
                     + ' · ' + esc(source.get('role', '')) + ' · ' + (str(count) + ' 則核對引文' if count else '僅書目／待補定位引文') + '</li>')
    parts.append('</ul><p>師承與時代不同；不將後續註家全算作玄奘親傳。轉引不算獨立證據。</p></details></section>')
    return ''.join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    sub.add_parser('verify')
    sub.add_parser('sources')
    candidate = sub.add_parser('candidate')
    candidate.add_argument('--issue', required=True)
    candidate.add_argument('--file', type=Path, required=True)
    reopen = sub.add_parser('reopen')
    reopen.add_argument('--issue', required=True)
    snapshot = sub.add_parser('snapshot'); snapshot.add_argument('--output', type=Path, required=True)
    compare = sub.add_parser('impact'); compare.add_argument('--baseline', type=Path, required=True)
    compare.add_argument('--output', type=Path)
    decision = sub.add_parser('decide')
    for flag in ('issue', 'choice', 'reviewer', 'rationale'):
        decision.add_argument('--' + flag, required=True)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        registry = load_registry(root)
        if args.command == 'candidate':
            result = update_issue(root, args.issue, read_json(args.file))
        elif args.command == 'reopen':
            result = update_issue(root, args.issue)
        elif args.command == 'sources':
            result = {key: registry.get(key, []) for key in ('sources', 'evidence', 'catalog')}
        elif args.command == 'snapshot':
            write_json(args.output, collect_state(root)); result = {'snapshot': str(args.output)}
        elif args.command == 'impact':
            result = impact(read_json(args.baseline), collect_state(root))
            if args.output: write_json(args.output, result)
        elif args.command == 'decide':
            result = decide(root, args.issue, args.choice, args.reviewer, args.rationale)
        elif args.command == 'verify':
            errors = [error for item in registry['evidence'] for error in verify_evidence(root, registry, item['id'])]
            result = {'errors': errors}
            print(json.dumps(result, ensure_ascii=False, indent=2)); return int(bool(errors))
        else:
            result = issue_list(root)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f'review-governance: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
