"""Conservative body receipts for task/source constraints, never filename facts.

Only explicit self declarations authorize hard exclusions. Unknown constraints
remain unknown; these receipts are navigation, not whole-answer verification.
"""
from collections import defaultdict
import re
import unicodedata

ID_PATTERN = r'(?<![A-Za-z0-9_])(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z][A-Za-z0-9_-]{2,63}(?![A-Za-z0-9_])'
ORG_PATTERN = r'[\u4e00-\u9fffA-Za-z]+?(?:研究院|研究站|观测站|实验室|大学|公司)(?:附属组|分公司|分院)?'


def normalize(text):
    return unicodedata.normalize('NFKC', text)


def identifiers(text):
    return tuple(dict.fromkeys(re.findall(ID_PATTERN, normalize(text))))


def contains_id(text, code):
    return bool(re.search(r'(?<![A-Za-z0-9_])'+re.escape(code)+r'(?![A-Za-z0-9_])', normalize(text), re.I))


def bind_sources(query, records):
    query = normalize(query)
    years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', query))
    versions = set(re.findall(r'(?:版本|version)\s*[:：]?\s*(v?[\d]+(?:\.[\d]+)*)', query, re.I))
    orgs = set(re.findall(ORG_PATTERN, query))
    codes = identifiers(query)
    grouped = defaultdict(list)
    for record in records:
        grouped[record.metadata['document_id']].append(record)
    receipts, rejected, series = {}, {}, defaultdict(list)
    for did, source in grouped.items():
        evidence = []
        declared_years, declared_versions = set(), set()
        declared_codes = set()
        header = None
        declared_orgs = set()
        reason = None
        for record in source:
            body = normalize(record.content)
            for pattern, target, role in (
                (r'(?:年份|适用年度|有效年度)\s*[:：]?\s*((?:19|20)\d{2})', declared_years, 'declared_year'),
                (r'(?:本文件版本|版本|version)\s*[:：]?\s*(v?\d+(?:\.\d+)*)', declared_versions, 'declared_version'),
                (r'(?:对象代码|对象编号|样本编号)\s*[:：]?\s*('+ID_PATTERN+')', declared_codes, 'declared_object')):
                for match in re.finditer(pattern, body, re.I):
                    target.add(match.group(1))
                    evidence.append({'chunk_id': record.document_id, 'source_sha256': record.metadata.get('source_sha256'),
                        'page_no': record.metadata.get('page_no'), 'source_locator': record.metadata.get('source_locator'),
                        'quote': match.group(), 'role': role})
            rules = [(r'(?:本文件|本版本|该版本|本公报|本记录)(?:已|已经)?(?:撤销|作废|失效)', 'self_revoked')]
            for code in codes:
                rules.append((r'(?:不适用(?:于)?|不得用于|仅排除)\s*(?:代码|对象|编号|样本)?\s*'+re.escape(code)+r'(?![A-Za-z0-9_])', 'object_excluded'))
            for year in years:
                rules.append((r'(?:本文件|本版本|该版本|本公报)?\s*(?:不可作为|不适用(?:于)?|不得用于)\s*'+year+r'年?', 'year_excluded'))
            for org in orgs:
                # Role-bearing declarations distinguish a parent from an affiliate.
                # A mention of another record being outside this institution is
                # deliberately not a declaration about the current source.
                rules.append((r'(?:属于|发布机构[:：]?|机构[:：]?)'+re.escape(org)+r'(?:附属组|分公司|分院)', 'organization_mismatch'))
                rules.append((r'属于'+re.escape(org)+r'的(?:邻近|其他|另一)(?:机构|单位)', 'organization_mismatch'))
            for pattern, code_reason in rules:
                match = re.search(pattern, body, re.I)
                if match:
                    reason = code_reason
                    evidence.append({'chunk_id': record.document_id, 'source_sha256': record.metadata.get('source_sha256'),
                        'page_no': record.metadata.get('page_no'), 'source_locator': record.metadata.get('source_locator'),
                        'quote': match.group(), 'role': code_reason})
            match = re.search(r'('+ORG_PATTERN+r')([^。\n,]{0,24}(?:公报|规程|记录|手册))', body)
            if match and header is None:
                header = match.group()
                declared_orgs.add(match.group(1))
            for match in re.finditer(r'(?:发布机构|所属机构|机构)\s*[:：]\s*('+ORG_PATTERN+')', body):
                declared_orgs.add(match.group(1))
        if years and declared_years and not years.intersection(declared_years):
            reason = 'declared_year_mismatch'
        if versions and declared_versions and not versions.intersection(declared_versions):
            reason = 'declared_version_mismatch'
        if orgs and declared_orgs and not orgs.intersection(declared_orgs):
            reason = 'declared_organization_mismatch'
        if reason:
            rejected[did] = reason
        if header and (declared_years or declared_versions) and (not codes or all(c in declared_codes for c in codes)):
            series[(header, tuple(sorted(declared_codes)))].append(did)
        receipts[did] = {'document_id': did, 'source_sha256': source[0].metadata.get('source_sha256'),
            'declared_years': sorted(declared_years), 'declared_versions': sorted(declared_versions),
            'declared_objects': sorted(declared_codes), 'evidence': evidence,
            'declared_organizations': sorted(declared_orgs),
            'status': 'rejected' if reason else 'no_explicit_conflict', 'reason': reason}
    ambiguous = []
    if not years and not versions:
        for members in series.values():
            active = [did for did in members if did not in rejected]
            variants = {(tuple(receipts[did]['declared_years']), tuple(receipts[did]['declared_versions'])) for did in active}
            if len(variants) > 1:
                ambiguous.extend(active)
                for did in active:
                    rejected[did] = 'version_unspecified'
                    receipts[did].update(status='requires_clarification', reason='version_unspecified')
    return [r for r in records if r.metadata['document_id'] not in rejected], {
        'version': 'body-source-conditions-v1', 'status': 'requires_clarification' if ambiguous else 'bound',
        'requested': {'years': sorted(years), 'versions': sorted(versions), 'organizations': sorted(orgs), 'objects': list(codes)},
        'rejected_sources': rejected, 'ambiguous_sources': sorted(ambiguous), 'sources': receipts,
        'authority': 'explicit_body_declarations_only_not_filename_or_keyword_presence'}
