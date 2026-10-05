"""Source-bound document return and explicit clarification of vague references."""
from dataclasses import dataclass
import hashlib
import json
import re
from .clarification import clarification_ordinal
from .knowledge_store import SourceIntegrityError


def seal(payload):
    return {'payload':payload,'sha256':hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}


def document_context(question, result, knowledge):
    if result.get('status') != 'ok':
        return None
    documents = {}
    for citation in result.get('citations', []):
        metadata = citation.get('metadata', {})
        identifier, digest = metadata.get('document_id'), metadata.get('source_sha256')
        if not identifier or not digest:
            return None
        knowledge.verify_source(identifier, expected_sha256=digest)
        documents[identifier] = digest
    return seal({'question':question,'documents':documents}) if documents else None


@dataclass(frozen=True)
class DialogueReference:
    turn: object = None
    document_id: str | None = None
    digest: str | None = None
    followup: str = ''
    reason: str | None = None
    options: tuple = ()


class DocumentDialogueAgent:
    ID = re.compile(r'^回到文档查询编号\s*(q_[a-f0-9]{32})[，,:：\s]+(.+)$',re.I)
    TITLE = re.compile(r'^(?:查询|根据|回到)文档[“「"]([^”」"]+)[”」"][，,:：\s]+(.+)$')
    SWITCH = re.compile(r'^(?:换成|改用|同样的问题换成)文档[“「"]([^”」"]+)[”」"]$')
    POINTER = re.compile(r'^(?:那么|那)?(?P<pointer>它(?=的|呢|改成|换成|按|再查|再看|[，,:：]|$)|(?:刚才)?(?:这个|那个)(?=的|呢|改成|换成|按|再查|再看|[，,:：]|$)|这份文档|那份文档|这份资料|那份资料|上述文档|刚才那份文档|之前那份文档|前一份文档|后一份文档|前者|后者)(?:的)?(?P<tail>.*)$')
    SELECT = re.compile(r'^选择对话来源编号\s*(q_[a-f0-9]{32})$')

    def __init__(self, knowledge, sources, engine):
        self.knowledge, self.sources, self.engine = knowledge, sources, engine

    def _bound(self, turn, followup):
        state = turn.state or {}
        if state.get('route') == 'sql':
            from .sql_history_scope import _confirmed_replacement_context_valid
            if not _confirmed_replacement_context_valid(followup,turn.effective_question,state,self.engine):
                return DialogueReference(reason='dialogue_sql_source_changed')
            return DialogueReference(turn=turn,followup=followup or '再查同样的结果')
        record = state.get('document_context') or {}
        payload = record.get('payload')
        if (not isinstance(payload,dict) or seal(payload) != record
                or payload.get('question') != turn.effective_question
                or not isinstance(payload.get('documents'),dict) or len(payload['documents']) != 1):
            return DialogueReference(reason='dialogue_source_unverified')
        identifier,digest = next(iter(payload['documents'].items()))
        try:
            self.knowledge.verify_source(identifier,expected_sha256=digest)
        except (KeyError,OSError,ValueError,SourceIntegrityError):
            return DialogueReference(reason='dialogue_document_changed')
        return DialogueReference(turn=turn,document_id=identifier,digest=digest,
            followup=followup or turn.effective_question)

    def run(self, question, history, session_id):
        text = question.strip().rstrip('？?。！!')
        sources = self.sources.list_sources(session_id) if session_id else ()
        direct = self.TITLE.fullmatch(text)
        switch = self.SWITCH.fullmatch(text)
        if switch:
            if not history or (history[-1].state or {}).get('route')!='document':
                return DialogueReference(reason='dialogue_source_unavailable')
            base=self._bound(history[-1],'')
            if base.reason:
                return base
            documents=[d for d in self.knowledge.list_documents() if d['title']==switch[1]]
            if len(documents)!=1:
                return DialogueReference(reason='dialogue_document_title_not_unique')
            document=documents[0]
            return DialogueReference(document_id=document['document_id'],digest=document['sha256'],followup=base.followup)
        if direct:
            documents = [d for d in self.knowledge.list_documents() if d['title'] == direct[1]]
            if len(documents) != 1:
                return DialogueReference(reason='dialogue_document_title_not_unique')
            document = documents[0]
            return DialogueReference(document_id=document['document_id'],digest=document['sha256'],followup=direct[2])
        by_id = self.ID.fullmatch(text)
        if by_id:
            turn = next((t for t in sources if t.turn_id == by_id[1] and (t.state or {}).get('route')=='document'),None)
            return self._bound(turn,by_id[2]) if turn else DialogueReference(reason='dialogue_source_unavailable')
        if text.startswith(('回到文档查询编号','选择对话来源编号')) and not self.SELECT.fullmatch(text):
            return DialogueReference(reason='dialogue_source_unavailable')
        pending = (history[-1].state or {}).get('dialogue_reference_pending') if history else None
        ordinal, selection = clarification_ordinal(question), self.SELECT.fullmatch(text)
        if pending and (ordinal is not None or selection):
            payload = pending.get('payload')
            if (not isinstance(payload,dict) or seal(payload) != pending
                    or not isinstance(payload.get('candidates'),list) or not isinstance(payload.get('followup'),str)):
                return DialogueReference(reason='dialogue_reference_pending_invalid')
            candidates = payload['candidates']
            if ordinal is not None:
                if not candidates or ordinal != -1 and not 1 <= ordinal <= len(candidates):
                    return DialogueReference(reason='dialogue_reference_out_of_range',options=tuple(candidates),followup=payload['followup'])
                identifier = candidates[-1 if ordinal == -1 else ordinal-1]['value']
            else:
                identifier = selection[1]
                if identifier not in [item['value'] for item in candidates]:
                    return DialogueReference(reason='dialogue_reference_not_offered',options=tuple(candidates),followup=payload['followup'])
            turn = next((t for t in sources if t.turn_id == identifier),None)
            return self._bound(turn,payload['followup']) if turn else DialogueReference(reason='dialogue_source_unavailable')
        if selection:
            return DialogueReference(reason='dialogue_reference_not_offered')
        pointer = self.POINTER.fullmatch(text)
        if pointer is None:
            return None
        tail = pointer['tail'].strip('，,:： ')
        if tail in {'呢','再查一次','再看一次'}:
            tail = ''
        else:
            tail = tail.removesuffix('呢').strip()
        # A vague referent is never a license for a cross-source calculation.
        if not sources:
            return DialogueReference(reason='dialogue_source_unavailable')
        kind = pointer['pointer']
        candidates = [t for t in sources if (t.state or {}).get('route')=='document'] if '文档' in kind or '资料' in kind else list(sources)
        # Distinct successful scopes only; repeated follow-ups on a single
        # document do not manufacture multiple documents to choose from.
        unique = {}
        for turn in candidates:
            state = turn.state or {}
            key = json.dumps((state.get('document_context') or {}).get('payload',{}).get('documents'),sort_keys=True) if state.get('route')=='document' else turn.effective_question
            unique[(state.get('route'),key)] = turn
        candidates = list(unique.values())[-8:]
        if kind in {'这份文档','这份资料','上述文档'} and history and (history[-1].state or {}).get('document_context'):
            return self._bound(history[-1],tail)
        if kind in {'前者','后者','前一份文档','后一份文档'} and len(candidates)==2:
            return self._bound(candidates[0 if kind in {'前者','前一份文档'} else 1],tail)
        previous_state=(history[-1].state or {}) if history else {}
        latest_verified=bool(previous_state.get('document_context') or previous_state.get('executed_sql_context'))
        # A bare pronoun after a failed/unresolved turn cannot silently skip
        # that turn and borrow an older success. Ask even for one candidate.
        if len(candidates)==1 and (latest_verified or kind in {'之前那份文档','那份文档','那份资料'}):
            return self._bound(candidates[0],tail)
        options = tuple({'value':turn.turn_id,'label':('文档：' if (turn.state or {}).get('route')=='document' else '查询：')+turn.effective_question[:160],
            'question':'选择对话来源编号'+turn.turn_id} for turn in candidates)
        return DialogueReference(reason='dialogue_reference_ambiguous' if options else 'dialogue_source_unavailable',options=options,followup=tail)
