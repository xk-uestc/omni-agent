"""Versioned persistent BM25 statistics and inverted postings.

The index is a disposable cache beside a KnowledgeStore corpus. It binds every
posting to the current document revision and exact chunk ID. Any unreadable or
incompatible cache falls back to the established dynamic BM25 implementation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from threading import RLock
from math import log

from .cross_source import DocumentHit, DocumentRecord, JsonDocumentRetriever, _retrieval_text, _tokenize
from .text_quality import NORMALIZATION_ID

INDEX_SCHEMA_VERSION = 'f2-bm25-postings-v1'
TOKENIZER_VERSION = 'ict8-tokenize-cjk-2-3-latin-v1'


def corpus_fingerprint(records: list[DocumentRecord]) -> str:
    digest=hashlib.sha256()
    for row in records:
        meta=row.metadata or {}
        content_hash=hashlib.sha256(row.content.encode('utf-8')).hexdigest()
        payload=(row.document_id,row.title,meta.get('document_id'),meta.get('source_sha256'),
                 meta.get('chunk_id'),meta.get('source_locator'),content_hash,
                 tuple(meta.get('title_path') or ()))
        digest.update(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode())
        digest.update(b'\n')
    return digest.hexdigest()


class PersistentBM25Index:
    """Persistent exact-token BM25 cache with corruption-safe dynamic fallback."""
    def __init__(self,path: str | Path):
        self.path=Path(path)
        self._lock=RLock()
        self.last_status='not_initialized'
        self.last_build_ms=0.0
        self.last_index_bytes=None
        self.last_rebuilt=False
        self._stats_fingerprint=None
        self._stats=None

    def _connect(self):
        conn=sqlite3.connect(self.path,timeout=0.25)
        conn.execute('PRAGMA busy_timeout=250')
        return conn

    def _schema(self,conn):
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS index_meta (
              singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version TEXT NOT NULL,
              tokenizer_version TEXT NOT NULL, normalization_id TEXT NOT NULL,
              corpus_fingerprint TEXT NOT NULL, document_count INTEGER NOT NULL,
              average_length REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS index_documents (
              chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
              content_sha256 TEXT NOT NULL, document_length INTEGER NOT NULL,
              title_terms TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS index_postings (
              term TEXT NOT NULL, chunk_id TEXT NOT NULL, document_id TEXT NOT NULL,
              source_sha256 TEXT NOT NULL, frequency INTEGER NOT NULL, title_match INTEGER NOT NULL,
              PRIMARY KEY(term,chunk_id));
            CREATE INDEX IF NOT EXISTS index_postings_chunk ON index_postings(chunk_id);
            CREATE TABLE IF NOT EXISTS index_df (term TEXT PRIMARY KEY, document_frequency INTEGER NOT NULL);
        ''')

    def _build(self,records,fingerprint):
        started=time.perf_counter()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self._connect()) as conn:
            self._schema(conn)
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM index_postings')
            conn.execute('DELETE FROM index_documents')
            conn.execute('DELETE FROM index_df')
            df=Counter(); total_length=0; docs=[]; postings=[]
            for rec in records:
                meta=rec.metadata or {}
                source_sha=str(meta.get('source_sha256') or '')
                if len(source_sha)!=64:
                    raise ValueError('indexed chunk is missing a source SHA256')
                frequencies=Counter(_tokenize(_retrieval_text(rec)))
                title_terms=set(_tokenize('\n'.join((rec.title,*[str(x) for x in (meta.get('title_path') or [])]))))
                length=sum(frequencies.values());total_length+=length
                content_sha=hashlib.sha256(rec.content.encode('utf-8')).hexdigest()
                docs.append((rec.document_id,str(meta.get('document_id')),source_sha,content_sha,length,
                             json.dumps(sorted(title_terms),ensure_ascii=False,separators=(',',':'))))
                for term,freq in frequencies.items():
                    postings.append((term,rec.document_id,str(meta.get('document_id')),source_sha,freq,int(term in title_terms)))
                    df[term]+=1
            conn.executemany('INSERT INTO index_documents VALUES(?,?,?,?,?,?)',docs)
            conn.executemany('INSERT INTO index_postings VALUES(?,?,?,?,?,?)',postings)
            conn.executemany('INSERT INTO index_df VALUES(?,?)',df.items())
            conn.execute('INSERT OR REPLACE INTO index_meta VALUES(1,?,?,?,?,?,?)',
                (INDEX_SCHEMA_VERSION,TOKENIZER_VERSION,NORMALIZATION_ID,fingerprint,len(docs),
                 total_length/len(docs) if docs else 0.0))
            conn.commit()
        self.last_build_ms=(time.perf_counter()-started)*1000

    def _ensure(self,records):
        fingerprint=corpus_fingerprint(records)
        with self._lock:
            conn=self._connect()
            try:
                self._schema(conn)
                check=conn.execute('PRAGMA quick_check(1)').fetchone()
                if not check or check[0]!='ok':
                    raise sqlite3.DatabaseError('persistent retrieval index quick_check failed')
                row=conn.execute('SELECT schema_version,tokenizer_version,normalization_id,corpus_fingerprint '
                    'FROM index_meta WHERE singleton=1').fetchone()
                current=(INDEX_SCHEMA_VERSION,TOKENIZER_VERSION,NORMALIZATION_ID,fingerprint)
                if row!=current:
                    self.last_rebuilt=True
                    conn.close()
                    conn=None
                    self._build(records,fingerprint)
                    conn=self._connect()
                else:self.last_rebuilt=False
                return conn,fingerprint
            except Exception:
                if conn is not None:conn.close()
                raise

    def statistics(self,records):
        fingerprint=corpus_fingerprint(records)
        if fingerprint==self._stats_fingerprint:return self._stats
        conn,fingerprint=self._ensure(records)
        try:
            if fingerprint!=self._stats_fingerprint:
                row=conn.execute('SELECT document_count,average_length FROM index_meta WHERE singleton=1').fetchone()
                self._stats={'document_count':row[0],'average_length':row[1],
                    'document_frequency':Counter(dict(conn.execute('SELECT term,document_frequency FROM index_df')))}
                self._stats_fingerprint=fingerprint
            return self._stats
        finally:conn.close()

    def search(self,query: str,records: list[DocumentRecord],*,top_k=20,candidate_limit=100):
        """Return (hits, status); corrupt cache safely uses the established BM25."""
        try:
            with self._lock:
                started=time.perf_counter()
                conn,fingerprint=self._ensure(records)
                try:
                    hits=self._search_connection(conn,query,records,fingerprint,top_k,candidate_limit)
                finally:
                    conn.close()
                self.last_status='persistent_index'
                self.last_query_ms=(time.perf_counter()-started)*1000
                self.last_index_bytes=self.path.stat().st_size if self.path.exists() else None
                return hits,self.last_status
        except (sqlite3.DatabaseError,OSError,ValueError) as exc:
            try:
                hits=JsonDocumentRetriever(records).search(query,top_k=top_k,candidate_limit=candidate_limit)
            except Exception:
                raise
            self.last_status='dynamic_fallback:'+type(exc).__name__
            self.last_query_ms=None
            return hits,self.last_status

    @staticmethod
    def _search_connection(conn,query,records,fingerprint,top_k,candidate_limit):
        if type(candidate_limit) is not int or not 20<=candidate_limit<=100:
            raise ValueError('candidate_limit must be between 20 and 100')
        terms=tuple(dict.fromkeys(_tokenize(query)))
        if not terms:return []
        docs={r.document_id:r for r in records}
        meta=conn.execute('SELECT corpus_fingerprint,document_count,average_length FROM index_meta WHERE singleton=1').fetchone()
        if not meta or meta[0]!=fingerprint:raise sqlite3.DatabaseError('index corpus changed during query')
        n,avg=meta[1],max(1.0,float(meta[2]))
        df={}
        for start in range(0,len(terms),400):
            part=terms[start:start+400]
            df.update(conn.execute('SELECT term,document_frequency FROM index_df WHERE term IN ('+
                ','.join('?' for _ in part)+')',part))
        found=defaultdict(dict)
        for start in range(0,len(terms),400):
            part=terms[start:start+400]
            sql='SELECT term,chunk_id,document_id,source_sha256,frequency,title_match FROM index_postings WHERE term IN ('+','.join('?' for _ in part)+')'
            for term,chunk_id,source_id,source_sha,freq,title_match in conn.execute(sql,part):
                record=docs.get(chunk_id)
                if record is None or record.metadata.get('document_id')!=source_id or record.metadata.get('source_sha256')!=source_sha:
                    raise sqlite3.DatabaseError('stale source revision in persistent index')
                found[chunk_id][term]=(freq,bool(title_match))
        lengths={row[0]:row[1] for row in conn.execute('SELECT chunk_id,document_length FROM index_documents')}
        k1,b=JsonDocumentRetriever._K1,JsonDocumentRetriever._B
        hits=[]
        for chunk_id,matched in found.items():
            record=docs[chunk_id];length=lengths.get(chunk_id)
            if length is None:raise sqlite3.DatabaseError('index document row missing')
            contributions=[]
            for term in terms:
                if term not in matched:continue
                frequency,title_match=matched[term]
                frequency_doc=df.get(term,0)
                idf=log(1+(n-frequency_doc+0.5)/(frequency_doc+0.5))
                denom=frequency+k1*(1-b+b*length/avg)
                value=idf*(frequency*(k1+1)/denom)
                if title_match:value*=1.8
                contributions.append((term,value))
            score=sum(value for _,value in contributions)
            bounded=score/(score+1.0)
            ordered=tuple(term for term,_ in sorted(contributions,key=lambda item:(-item[1],item[0])))
            meta=record.metadata
            if str(meta.get('chunk_id') or chunk_id)!=chunk_id:
                raise sqlite3.DatabaseError('index chunk identity mismatch')
            hits.append(DocumentHit(chunk_id,record.title,round(min(1.0,bounded),4),ordered[:12],
                JsonDocumentRetriever._snippet(record.content,ordered),record.source_uri,
                {**meta,'bm25_raw':round(score,6),'retrieval_channel':'bm25',
                 'text_normalization':NORMALIZATION_ID}))
        hits.sort(key=lambda item:(-item.score,item.document_id))
        max_raw=max((float(item.metadata.get('bm25_raw',0)) for item in hits),default=0.0)
        hits=[DocumentHit(item.document_id,item.title,item.score,item.matched_terms,item.snippet,item.source_uri,
            {**item.metadata,'bm25_relative':round(float(item.metadata.get('bm25_raw',0))/max_raw,4) if max_raw else 0.0})
            for item in hits]
        return hits[:max(1,min(candidate_limit,int(top_k)))]


class PersistentFTS5Index:
    """SQLite FTS5 probe over the project's existing pre-tokenized terms.

    The deployed unicode61 tokenizer does not segment Chinese words; FTS5 here
    receives the established tokens separated by spaces, so this is a ranking
    engine comparison and does not pretend to replace the project tokenizer.
    """
    def __init__(self,path: str | Path):
        self.path=Path(path);self._lock=RLock();self.last_build_ms=0.0
        self._fingerprint=None;self._stats=None;self.last_status='not_initialized';self.last_index_bytes=None

    def _connect(self):
        c=sqlite3.connect(self.path,timeout=.25);c.execute('PRAGMA busy_timeout=250');return c

    def _ensure(self,records):
        fingerprint=corpus_fingerprint(records);self.path.parent.mkdir(parents=True,exist_ok=True)
        conn=self._connect()
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS f2_fts_probe USING fts5(chunk_id UNINDEXED,document_id UNINDEXED,source_sha256 UNINDEXED,title_terms,body_terms,tokenize='unicode61')")
            conn.execute('CREATE TABLE IF NOT EXISTS f2_fts_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version TEXT NOT NULL, tokenizer_version TEXT NOT NULL, normalization_id TEXT NOT NULL, corpus_fingerprint TEXT NOT NULL, document_count INTEGER NOT NULL, average_length REAL NOT NULL)')
            meta=conn.execute('SELECT schema_version,tokenizer_version,normalization_id,corpus_fingerprint FROM f2_fts_meta WHERE singleton=1').fetchone()
            expected=('f2-fts5-probe-v1',TOKENIZER_VERSION,NORMALIZATION_ID,fingerprint)
            self.last_rebuilt=meta!=expected
            if self.last_rebuilt:
                start=time.perf_counter();conn.execute('BEGIN IMMEDIATE');conn.execute('DELETE FROM f2_fts_probe');
                df=Counter();lengths=0
                rows=[]
                for r in records:
                    m=r.metadata or {};source_sha=str(m.get('source_sha256') or '')
                    if len(source_sha)!=64:raise ValueError('FTS5 source SHA missing')
                    full=_tokenize(_retrieval_text(r));title=set(_tokenize('\n'.join((r.title,*[str(x) for x in (m.get('title_path') or [])]))))
                    body=full;lengths+=len(full)
                    df.update(set(full))
                    rows.append((r.document_id,str(m.get('document_id')),source_sha,' '.join(sorted(title)),' '.join(body)))
                conn.executemany('INSERT INTO f2_fts_probe VALUES(?,?,?,?,?)',rows)
                conn.execute('INSERT OR REPLACE INTO f2_fts_meta VALUES(1,?,?,?,?,?,?)',(*expected[:3],fingerprint,len(rows),lengths/len(rows) if rows else 0.0));conn.commit()
                self.last_build_ms=(time.perf_counter()-start)*1000
                self._stats={'document_count':len(rows),'average_length':lengths/len(rows) if rows else 0.0,'document_frequency':df}
            elif self._fingerprint!=fingerprint:
                raise sqlite3.DatabaseError('FTS5 metadata changed unexpectedly')
            self._fingerprint=fingerprint
            return conn,fingerprint
        except Exception:
            conn.close();raise

    def statistics(self,records):
        if self._fingerprint==corpus_fingerprint(records):return self._stats
        conn,fp=self._ensure(records);conn.close();return self._stats

    def search(self,query,records,*,top_k=20,candidate_limit=100):
        try:
            with self._lock:
                conn,fp=self._ensure(records)
                try:hits=self._search(conn,query,records,fp,top_k,candidate_limit)
                finally:conn.close()
            self.last_status='fts5_unicode61_with_project_tokens'
            self.last_index_bytes=self.path.stat().st_size if self.path.exists() else None
            return hits,self.last_status
        except (sqlite3.DatabaseError,OSError,ValueError) as exc:
            self.last_status='dynamic_fallback:'+type(exc).__name__
            return JsonDocumentRetriever(records).search(query,top_k=top_k,candidate_limit=candidate_limit),self.last_status

    @staticmethod
    def _search(conn,query,records,fingerprint,top_k,candidate_limit):
        if type(candidate_limit) is not int or not 20<=candidate_limit<=100:raise ValueError('invalid candidate limit')
        terms=tuple(dict.fromkeys(_tokenize(query)))
        if not terms:return []
        expr=' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
        rows=conn.execute('SELECT chunk_id,document_id,source_sha256,bm25(f2_fts_probe,0,0,0,1.8,1.0) '
            'FROM f2_fts_probe WHERE f2_fts_probe MATCH ? ORDER BY 4,chunk_id LIMIT ?',
            (expr,max(1,min(candidate_limit,int(top_k))))).fetchall()
        docs={r.document_id:r for r in records};hits=[]
        for chunk_id,source_id,source_sha,rank in rows:
            r=docs.get(chunk_id)
            if r is None or r.metadata.get('document_id')!=source_id or r.metadata.get('source_sha256')!=source_sha:
                raise sqlite3.DatabaseError('stale FTS source revision')
            matched=tuple(term for term in terms if term in _tokenize(_retrieval_text(r)))
            raw=max(0.0,-float(rank));hits.append(DocumentHit(chunk_id,r.title,round(raw/(raw+1),4),matched[:12],
                JsonDocumentRetriever._snippet(r.content,matched),r.source_uri,
                {**r.metadata,'bm25_raw':raw,'retrieval_channel':'fts5','text_normalization':NORMALIZATION_ID}))
        max_raw=max((h.metadata['bm25_raw'] for h in hits),default=0.0)
        return [DocumentHit(h.document_id,h.title,h.score,h.matched_terms,h.snippet,h.source_uri,
            {**h.metadata,'bm25_relative':round(h.metadata['bm25_raw']/max_raw,4) if max_raw else 0.0}) for h in hits]
