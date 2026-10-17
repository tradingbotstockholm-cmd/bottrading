"""On-chain and Rugcheck safety evidence for strict and PAPER exploration.

Exploratory mode does NOT certify safety: when only one independent evidence
source works, a provisional paper-only decision may be granted at minimum size.
Positive critical findings never become acceptable because an API failed.
"""
from __future__ import annotations
from dataclasses import dataclass
from hunter.http import get_json
from hunter.rug import SolanaRPC

@dataclass(frozen=True)
class SecurityVerdict:
    approved: bool
    reasons: tuple[str, ...]
    score: int|None = None
    provisional: bool = False
    notes: tuple[str, ...] = ()

CRITICAL={'danger','critical','error'}

class SecurityGate:
    def __init__(self,rpc_url:str, max_rug_score:int=20, require_rugcheck:bool=True,
                 paper_exploratory:bool=False, paper_rug_score_ceiling:int=45):
        self.rpc=SolanaRPC(rpc_url)
        self.max_rug_score=max_rug_score
        self.require_rugcheck=require_rugcheck
        self.paper_exploratory=paper_exploratory
        self.paper_rug_score_ceiling=paper_rug_score_ceiling

    def examine(self,mint:str,curve_owner:str|None=None) -> SecurityVerdict:
        problems=[];notes=[];score=None
        try:
            rpc=self.rpc.examine(mint,curve_owner)
            rpc_reasons=list(rpc.reasons) if not rpc.allowed else []
        except Exception as exc:
            rpc_reasons=['risk data unavailable: '+type(exc).__name__]
        # A temporary RPC outage is NOT proof of a rug. Also not proof of safety.
        # Only a recognized provider outage is eligible for provisional PAPER.
        outage=bool(rpc_reasons) and all(
            r.startswith('risk data unavailable:') and
            any(marker in r.lower() for marker in ('429','rate limit','too many requests','timeout','timed out','503'))
            for r in rpc_reasons
        )
        if rpc_reasons:
            if self.paper_exploratory and outage:
                notes.append('on-chain rug audit unavailable (RPC throttled); independently corroborated only')
            else:
                problems.extend('rpc: '+str(r) for r in rpc_reasons)
        if not curve_owner:
            problems.append('curve custody address unavailable: cannot validate concentration')
        report_valid=False
        try:
            obj=get_json(f'https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary',timeout=8)
            if not isinstance(obj,dict):raise ValueError('not a JSON object')
            raw_score=obj.get('score_normalised')
            if not isinstance(raw_score,(int,float)) or not 0<=raw_score<=100:
                raise ValueError('normalized score absent')
            score=int(raw_score)
            risks=obj.get('risks')
            if not isinstance(risks,list):raise ValueError('risk list absent')
            for item in risks:
                if not isinstance(item,dict):raise ValueError('risk entry malformed')
                if str(item.get('level','')).lower() in CRITICAL:
                    problems.append('rugcheck: '+str(item.get('name','unclassified critical risk')))
            report_valid=True
            if score>(self.paper_rug_score_ceiling if self.paper_exploratory else self.max_rug_score):
                problems.append(f'rugcheck score {score} exceeds paper/strict ceiling')
            elif self.paper_exploratory and score>self.max_rug_score:
                notes.append(f'rugcheck score {score} exceeds strict {self.max_rug_score}')
        except Exception as exc:
            if self.paper_exploratory and not rpc_reasons:
                notes.append('rugcheck report unavailable; on-chain audit only ('+type(exc).__name__+')')
            elif self.require_rugcheck:
                problems.append('rugcheck unavailable/incomplete: '+type(exc).__name__)
        if outage and not report_valid:
            problems.append('both rug evidence sources unavailable; cannot admit even provisional paper entry')
        if problems:
            return SecurityVerdict(False,tuple(dict.fromkeys(problems)),score)
        provisional=self.paper_exploratory and bool(notes)
        return SecurityVerdict(True,(),score,provisional,tuple(notes))
