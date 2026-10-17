"""Actual local BGE embeddings with content-versioned cache and cosine/RRF audit."""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from pathlib import Path

from .cross_source import DocumentHit, _retrieval_text
from .text_quality import simplify_for_retrieval, NORMALIZATION_ID


class DenseRetrievalError(ValueError):
    pass


class LocalBgeEmbedder:
    def __init__(self, model_path):
        self.path = Path(model_path)
        self._lock = threading.Lock()
        self._model = self._tokenizer = None
        manifest = json.loads((self.path / 'ASSET_MANIFEST.json').read_text(encoding='utf-8'))
        self.identity = manifest['model'] + '@' + manifest['revision']
        # This bundled encoder is a Chinese model. Do not present its English
        # similarity scores as an equally supported retrieval channel.
        self.supported_query_languages = ('zh',) if manifest['model'] == 'BAAI/bge-small-zh-v1.5' else None
        for item in manifest['files']:
            file = self.path / Path(item['path']).name
            if hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
                raise DenseRetrievalError('向量模型文件完整性核验失败')

    def embed(self, texts, *, query=False):
        if not texts or len(texts) > 64 or any(not isinstance(text, str) or not text.strip() or len(text) > 4000 for text in texts):
            raise DenseRetrievalError('向量输入为空或超出资源限制')
        with self._lock:
            import torch
            if self._model is None:
                os.environ.setdefault('USE_TF', '0')
                from transformers import AutoModel, AutoTokenizer
                torch.set_num_threads(2)
                self._tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True, trust_remote_code=False)
                self._model = AutoModel.from_pretrained(self.path, local_files_only=True, trust_remote_code=False, use_safetensors=True).eval()
            prefixed = [('为这个句子生成表示以用于检索相关文章：' + simplify_for_retrieval(text)) if query else simplify_for_retrieval(text) for text in texts]
            tokens = self._tokenizer(prefixed, padding=True, truncation=True, max_length=512, return_tensors='pt')
            with torch.inference_mode():
                output = self._model(**tokens).last_hidden_state[:, 0]
                vectors = torch.nn.functional.normalize(output, p=2, dim=1).tolist()
        return vectors


class DenseIndex:
    def __init__(self, store, embedder):
        self.store, self.embedder = store, embedder
        self._lock = threading.Lock()
        with store.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS vectors (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def vectors(self, records):
        texts = [_retrieval_text(record) for record in records]
        keys = [hashlib.sha256((self.embedder.identity + '\0' + NORMALIZATION_ID + '\0' + text).encode()).hexdigest() for text in texts]
        with self._lock:
            with self.store.connect() as connection:
                # A scoped request must not deserialize every historical model
                # and source revision. Keep keys content/model/normalization bound.
                cached = {}
                wanted = list(dict.fromkeys(keys))
                for start in range(0, len(wanted), 500):
                    batch = wanted[start:start+500]
                    placeholders = ','.join('?' for _ in batch)
                    for key, payload in connection.execute(
                            f'SELECT cache_key,payload FROM vectors WHERE cache_key IN ({placeholders})', batch):
                        try:
                            vector = json.loads(payload)
                            self.validate_vector(vector)
                        except (ValueError, TypeError) as exc:
                            raise DenseRetrievalError('缓存向量损坏，请重建受影响的索引') from exc
                        cached[key] = vector
            missing = list(dict.fromkeys(key for key in keys if key not in cached))
            by_key = dict(zip(keys, texts))
            for start in range(0, len(missing), 16):
                batch = missing[start:start+16]
                vectors = self.embedder.embed([by_key[key] for key in batch])
                if len(vectors) != len(batch):
                    raise DenseRetrievalError('向量结果数量不匹配')
                with self.store.connect() as connection:
                    for key, vector in zip(batch, vectors):
                        self.validate_vector(vector)
                        cached[key] = vector
                        connection.execute('INSERT OR REPLACE INTO vectors VALUES (?,?)', (key, json.dumps(vector)))
        return [cached[key] for key in keys]

    @staticmethod
    def validate_vector(vector):
        if not isinstance(vector, list) or not 2 <= len(vector) <= 8192 or any(isinstance(x, bool) or not isinstance(x, (int,float)) or not math.isfinite(x) for x in vector):
            raise DenseRetrievalError('向量格式无效')
        norm = math.sqrt(sum(x*x for x in vector))
        if norm == 0:
            raise DenseRetrievalError('零向量不可用于语义检索')
        return norm

    def search(self, query, records, *, top_k=20):
        if not records:
            return []
        query = simplify_for_retrieval(query)
        vector = self.embedder.embed([query], query=True)[0]
        query_norm = self.validate_vector(vector)
        ranked = []
        for record, embedding in zip(records, self.vectors(records)):
            if len(embedding) != len(vector):
                raise DenseRetrievalError('文档与问题的向量维度不一致')
            similarity = sum(x*y for x,y in zip(vector, embedding)) / (query_norm * self.validate_vector(embedding))
            ranked.append((record, max(-1.0, min(1.0, similarity))))
        ranked.sort(key=lambda row: (-row[1], row[0].document_id))
        return [DocumentHit(record.document_id, record.title, round(score,6), (), record.content[:400], record.source_uri,
                {**record.metadata, 'dense_cosine': round(score,6), 'dense_rank': rank, 'embedding_model': self.embedder.identity, 'retrieval_channel': 'dense'})
                for rank,(record,score) in enumerate(ranked[:top_k],1)]


def reciprocal_rank_fusion(bm25, dense, *, rank_constant=60):
    merged, scores = {}, {}
    for channel, hits in [('bm25',bm25),('dense',dense)]:
        for rank, hit in enumerate(hits, 1):
            scores[hit.document_id] = scores.get(hit.document_id, 0) + 1/(rank_constant+rank)
            prior = merged.get(hit.document_id)
            metadata = {**(prior.metadata if prior else {}), **hit.metadata, channel+'_rank':rank}
            channels = list(dict.fromkeys([*(prior.metadata.get('retrieval_channels',[]) if prior else []),channel]))
            metadata.update({'retrieval_channels':channels,'retrieval_channel':'hybrid'})
            merged[hit.document_id] = DocumentHit(hit.document_id,hit.title,hit.score, prior.matched_terms if prior and prior.matched_terms else hit.matched_terms,
                prior.snippet if prior else hit.snippet,hit.source_uri,metadata)
    ordered = sorted(merged.values(),key=lambda hit:(-scores[hit.document_id],hit.document_id))
    return [DocumentHit(hit.document_id,hit.title,scores[hit.document_id],hit.matched_terms,hit.snippet,hit.source_uri,
            {**hit.metadata,'rrf_rank':rank,'rrf_score':scores[hit.document_id]}) for rank,hit in enumerate(ordered,1)]
