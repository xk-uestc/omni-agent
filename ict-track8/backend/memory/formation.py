"""Source-bound candidates and local review. No HTTP promotion authority."""
from __future__ import annotations
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import unicodedata
from .core import MemoryRecord, RecallContext, TrustedScope, encoded, timestamp
from .extraction import PREFIX, digest, extract_candidate, candidate_digest


@dataclass(frozen=True)
class LocalReviewContext:
    uid: int
    account: str
    host: str
    configuration_sha256: str
    memory_path: str
    scope_key: str

    @classmethod
    def from_config(cls,path):
        path=Path(path)
        info=path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o022:
            raise PermissionError('local review config must be owned by current OS user and not group/world writable')
        config=json.loads(path.read_text())
        scope=TrustedScope(**config['scope'])
        return cls(os.geteuid(),pwd.getpwuid(os.geteuid()).pw_name,socket.gethostname(),digest(config),
            str(Path(config['memory_db']).resolve()),scope.key)


class MemoryFormation:
    memory_type = 'business_semantics'
    def __init__(self,adapter):
        self.adapter=adapter
        self.store=adapter.core.store
        self.scope=adapter.core.scope

    def candidates(self):
        with self.store.connect() as db:
            rows=db.execute('SELECT payload,state,receipt FROM memory_candidates WHERE scope=? ORDER BY candidate_id',(self.scope.key,)).fetchall()
        return [{**json.loads(p),'verification_state':s,'validation':json.loads(v)} for p,s,v in rows if json.loads(p).get('memory_type') == self.memory_type]

    def candidate(self,ident):
        matches=[c for c in self.candidates() if c['candidate_id']==ident]
        if len(matches)!=1:raise ValueError('candidate missing in trusted scope')
        c=matches[0]
        if candidate_digest(c)!=c['digest'] or c['candidate_id']!='cand-'+c['digest'][:32]:
            raise ValueError('candidate digest changed')
        return c

    def capture(self,response,event_id):
        """Consume only actual document-result citations; never user statements."""
        if response.get('route')!='document' or response.get('status')!='ok':return []
        grouped={}
        for citation in (response.get('result') or {}).get('citations',[]):
            meta=citation.get('metadata',{})
            if meta.get('document_id') in self.scope.data_sources:
                grouped.setdefault(meta['document_id'],[]).append(meta)
        ids=[]
        for did,metas in grouped.items():
            doc=self.adapter.knowledge.document(did)
            if doc['modality']!='txt' or any(m.get('source_sha256')!=doc['sha256'] for m in metas):continue
            asset=self.adapter.knowledge.verify_source(did,expected_sha256=doc['sha256'])
            chunks={c['chunk_id']:c for c in doc['chunks']}
            cited=[chunks[m['chunk_id']] for m in metas if m.get('chunk_id') in chunks]
            version=self.adapter.source_version((did,))
            event={'event_id':event_id,'kind':'verified_document_read','document_id':did,'source_sha256':doc['sha256'],
                'chunk_ids':sorted(c['chunk_id'] for c in cited),'observed_at':self.adapter.clock()}
            found=[]
            for number,line in enumerate(asset.read_text(encoding='utf-8').splitlines(),1):
                line=line.strip()
                if not PREFIX.fullmatch(line):continue
                matching=[c for c in cited if unicodedata.normalize('NFKC',line) in unicodedata.normalize('NFKC',c['text'])]
                if not matching:continue
                evidence={'source_id':did,'document_id':did,'source_sha256':doc['sha256'],
                    'chunk_id':matching[0]['chunk_id'],'locator':{'line':number},'quote':line}
                try:candidate=extract_candidate(event,evidence,version,self.scope,self.adapter.clock())
                except (ValueError,TypeError):continue
                if candidate:found.append(candidate)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old=db.execute('SELECT payload FROM memory_source_events WHERE scope=? AND event_id=? AND document_id=?',(self.scope.key,event_id,did)).fetchone()
                if old and json.loads(old[0])['source_sha256']!=doc['sha256']:raise ValueError('event replay source mismatch')
                db.execute('INSERT OR IGNORE INTO memory_source_events VALUES(?,?,?,?)',(self.scope.key,event_id,did,encoded(event)))
                for c in found:
                    db.execute('INSERT OR IGNORE INTO memory_candidates VALUES(?,?,?,?,?,?)',
                        (self.scope.key,c['candidate_id'],c['digest'],encoded(c),'candidate','{}'))
                    db.execute('INSERT OR IGNORE INTO memory_candidate_events VALUES(?,?,?)',(self.scope.key,c['candidate_id'],event_id))
                    ids.append(c['candidate_id'])
        return sorted(set(ids))

    def _record(self,c,state='confirmed',review=None):
        review=review or {}
        return MemoryRecord(memory_id='mem-'+c['candidate_id'][5:],memory_type='business_semantics',term=c['term'],
            content=c['definition'],binding=c['binding'],scope=self.scope,
            provenance={'authority':'trusted_local_review' if review else 'validation_only_not_confirmation',
                'confirmation_id':review.get('request_id','not-confirmed'), 'verification_id':c['digest'],
                'evidence_id':c['evidence']['chunk_id'],'candidate_id':c['candidate_id'],'candidate_digest':c['digest'],
                'evidence':c['evidence'],'review':review},source_version=c['source_version'],
            valid_from=c['valid_from'],valid_to=c['valid_to'],verification_state=state,
            created_at=c['created_at'],updated_at=self.adapter.clock(),task_trace_id=c['source_event_id'])

    def _check(self,c,*,ignore_memory=None,check_conflicts=True):
        reasons=[]
        if TrustedScope(**c['scope'])!=self.scope:return ['foreign_scope']
        e=c['evidence'];did=e['document_id']
        try:
            doc=self.adapter.knowledge.document(did)
            path=self.adapter.knowledge.verify_source(did,expected_sha256=e['source_sha256'])
            raw=path.read_text(encoding='utf-8').splitlines()
            line=raw[e['locator']['line']-1].strip()
            if line!=e['quote']:return ['evidence_locator_mismatch']
            chunk=next(x for x in doc['chunks'] if x['chunk_id']==e['chunk_id'])
            if unicodedata.normalize('NFKC',line) not in unicodedata.normalize('NFKC',chunk['text']):return ['evidence_chunk_mismatch']
            contract=json.loads(PREFIX.fullmatch(line)[1])
            if any(contract.get(k)!=c.get(k) for k in ('term','binding','valid_from','valid_to')) or contract['definition']!=c['definition']:
                return ['source_contract_mismatch']
            if not isinstance(c['binding'],dict):return ['missing_explicit_binding']
            timestamp(c['valid_from'])
            if c['valid_to'] and timestamp(c['valid_to'])<=timestamp(c['valid_from']):return ['invalid_validity']
            schema,sources,metrics,values,rules,_=self.adapter._snapshot()
            context=RecallContext(c['term'],self.adapter.clock(),schema,sources,metrics,values)
            reason=self.adapter.core.invalid_reason(self._record(c),context,self.scope)
            if reason:return [reason]
            b=c['binding']
            for rule in rules:
                if c['term'] in rule.aliases and (rule.table,rule.column)!=(b['table'],b['column']):reasons.append('catalog_alias_conflict')
            if check_conflicts:
                for other in self.candidates():
                    if (other['candidate_id']!=c['candidate_id'] and other['term']==c['term'] and other['verification_state'] not in {'rejected','revoked','superseded'}
                        and encoded(other['binding'])!=encoded(c['binding']) and not self._check(other,check_conflicts=False)):
                        reasons.append('candidate_conflict')
                for other in self.store.scan(self.scope)[0]:
                    if other.memory_id==ignore_memory:continue
                    if other.term==c['term'] and encoded(other.binding)!=encoded(c['binding']) and self.adapter.core.invalid_reason(other,context,self.scope) is None:
                        reasons.append('confirmed_conflict')
        except (OSError,ValueError,TypeError,KeyError,IndexError,StopIteration):
            return ['source_or_contract_unverifiable']
        return sorted(set(reasons))

    def validate(self,ident,expected_digest):
        c=self.candidate(ident)
        if c['digest']!=expected_digest:raise ValueError('candidate digest changed')
        if c['verification_state'] in {'rejected','revoked','superseded','confirmed'}:raise ValueError('terminal candidate needs new source version')
        receipt={'valid':False,'checked_at':self.adapter.clock(),'candidate_digest':expected_digest,'checks':'source_contract+M1B1_invalid_reason+scope_conflicts' if self.memory_type=='business_semantics' else 'typed_experience+immutable_verified_trace+shared_context_validity'}
        receipt['reasons']=self._check(c);receipt['valid']=not receipt['reasons']
        with self.store.connect() as db:
            db.execute('UPDATE memory_candidates SET state=?,receipt=? WHERE scope=? AND candidate_id=? AND digest=? AND state IN (\'candidate\',\'validated\')',
                ('validated' if receipt['valid'] else 'candidate',encoded(receipt),self.scope.key,ident,expected_digest))
        return receipt

    def _authority(self,context):
        if (not isinstance(context,LocalReviewContext) or context.uid!=os.geteuid() or context.memory_path!=str(self.store.path.resolve())
                or context.scope_key!=self.scope.key):raise PermissionError('trusted local review context required')

    def review(self,ident,expected_digest,*,context,request_id,decision,reason,supersedes=None):
        self._authority(context)
        if not isinstance(reason,str):raise ValueError('review reason required')
        if decision not in {'confirm','reject'} or not reason.strip() or not request_id:raise ValueError('explicit decision/reason/id required')
        request={'candidate_id':ident,'digest':expected_digest,'request_id':request_id,'decision':decision,'reason':reason,
            'reviewer':asdict(context),'supersedes':supersedes};request_hash=digest(request)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT request_digest,payload FROM memory_reviews WHERE scope=? AND request_id=?',(self.scope.key,request_id)).fetchone()
            if prior:
                if prior[0]!=request_hash:raise ValueError('review id reused with changed request')
                return json.loads(prior[1])
            c=self.candidate(ident)
            if c['digest']!=expected_digest:raise ValueError('candidate digest changed')
            if c['verification_state'] not in {'candidate','validated'}:raise ValueError('terminal candidate cannot be reapproved')
            if decision=='confirm' and c['verification_state']!='validated':raise ValueError('validation required before confirmation')
            checks=self._check(c,ignore_memory=supersedes) if decision=='confirm' else []
            if checks:raise ValueError('promotion refused: '+','.join(checks))
            receipt={**request,'reviewed_at':self.adapter.clock(),'validation':c['validation'],
                'source_version':c['source_version'],'memory_id':None}
            if decision=='confirm':
                if supersedes:
                    old=self._read_memory(db,supersedes)
                    if old.term!=c['term']:raise ValueError('superseded memory term differs')
                    self.store.write_record(db,replace(old,verification_state='superseded',updated_at=self.adapter.clock()),'superseded_by:'+ident)
                    db.execute('UPDATE memory_candidates SET state=\'superseded\' WHERE scope=? AND candidate_id=?',
                        (self.scope.key,old.provenance.get('candidate_id','')))
                record=self._record(c,review=receipt);receipt['memory_id']=record.memory_id
                # The second check is immediately before the transactional write.
                checks=self._check(c,ignore_memory=supersedes)
                if checks:raise ValueError('source changed before write: '+','.join(checks))
                self.store.write_record(db,record,'promoted:'+request_id)
            db.execute('UPDATE memory_candidates SET state=? WHERE scope=? AND candidate_id=? AND digest=?',
                ('confirmed' if decision=='confirm' else 'rejected',self.scope.key,ident,expected_digest))
            db.execute('INSERT INTO memory_reviews VALUES(?,?,?,?)',(self.scope.key,request_id,request_hash,encoded(receipt)))
        return receipt

    def _read_memory(self,db,ident):
        row=db.execute('SELECT payload FROM memories WHERE scope=? AND memory_id=?',(self.scope.key,ident)).fetchone()
        if not row:raise ValueError('memory missing in trusted scope')
        value=json.loads(row[0]);value['scope']=TrustedScope(**value['scope']);return MemoryRecord(**value)

    def revoke(self,ident,expected_digest,*,context,request_id,reason):
        self._authority(context)
        if not reason.strip() or not request_id:raise ValueError('revocation reason/id required')
        request={'memory_id':ident,'digest':expected_digest,'request_id':request_id,'decision':'revoke','reason':reason,'reviewer':asdict(context)}
        rh=digest(request)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT request_digest,payload FROM memory_reviews WHERE scope=? AND request_id=?',(self.scope.key,request_id)).fetchone()
            if prior:
                if prior[0]!=rh:raise ValueError('review id reused')
                return json.loads(prior[1])
            old=self._read_memory(db,ident)
            if digest(asdict(old))!=expected_digest:raise ValueError('memory digest changed')
            self.store.write_record(db,replace(old,verification_state='revoked',updated_at=self.adapter.clock()),'revoked:'+request_id)
            db.execute('UPDATE memory_candidates SET state=\'revoked\' WHERE scope=? AND candidate_id=?',(self.scope.key,old.provenance.get('candidate_id','')))
            receipt={**request,'reviewed_at':self.adapter.clock()}
            db.execute('INSERT INTO memory_reviews VALUES(?,?,?,?)',(self.scope.key,request_id,rh,encoded(receipt)))
        return receipt
