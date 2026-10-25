"""Deliberately weakened evaluation-only baselines; never production switches."""
import time
from dataclasses import replace

from phantom.contracts import to_csv_row
from phantom.identities import IdentityIndex
from phantom.metrics import compare_rows
from phantom.pipeline import audit_request
from phantom.planner import AuditBlocked
from phantom.state import StateIndex


def measure_ablations(context, requests, expected):
    output=[]
    for name in ('full','without_historical_identities','without_replicas','without_neural_images'):
        started=time.perf_counter()
        facts=context.states.facts;scopes=context.states.scopes
        if name=='without_replicas':
            facts=tuple(f for f in facts if 'replica' not in f.entity.store_id.casefold())
            scopes=tuple(s for s in scopes if 'replica' not in s.store_id.casefold())
        if name=='without_neural_images':
            facts=tuple(f for f in facts if not f.source.source_path.lower().endswith(('.png','.jpg','.jpeg')))
        identities=IdentityIndex(context.tables['user_identities'],
            () if name=='without_historical_identities' else context.tables['user_audit_log'],
            () if name=='without_historical_identities' else facts)
        variant=replace(context,identities=identities,states=StateIndex(facts,scopes))
        predictions=[];blocked=0
        for request in requests:
            try:predictions.append(to_csv_row(audit_request(variant,request)))
            except AuditBlocked:blocked+=1
        metrics=compare_rows(expected,predictions)
        output.append(dict(name=name,metrics=metrics,blocked=blocked,seconds=time.perf_counter()-started))
    return output
