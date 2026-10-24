"""Independent filesystem cases with explicit expected plans, not planner-derived labels."""
import csv
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from phantom.contracts import AuditResult, AuditStep, to_csv_row
from phantom.pipeline import audit_request, prepare
from phantom.tabular import TABLE_SPECS
from phantom.verifier import verify_result

A="2026-01-01T00:00:00Z"
B="2026-02-01T00:00:00Z"
C="2026-03-01T00:00:00Z"
D="2026-04-01T00:00:00Z"
E="2026-05-01T00:00:00Z"
EMAIL="person@example.test"


def _csv(path, columns, rows):
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader();writer.writerows(rows)


def snapshot(system, collection, records, time=C):
    return dict(captured_at=time,data_store=system,collection=collection,records=records)


def bundle(*snapshots):
    return dict(exported_at=C,snapshots=list(snapshots))


def absence(time=B):
    return dict(exported_at=time,origin='independent readback',record_count=1,records=[dict(
        recorded_at=time,system='account_service',resource_id='opaque-alpha',record_type='USER_PROFILE',
        result='NOT_FOUND',lookup_value=EMAIL)])


def expected(case, status, descriptions):
    return to_csv_row(AuditResult(case,status,tuple(AuditStep(i,*step) for i,step in enumerate(descriptions,1))))


def deletion_steps(evidence, resource='opaque-alpha', system='account_service'):
    return [('FINDING',system,resource,'REPORT_REMAINING_RECORD','PRESENT',evidence),
            ('REMEDIATION',system,resource,'DELETE_RECORD','ABSENT',''),
            ('VERIFICATION',system,resource,'VERIFY_RECORD_ABSENT','ABSENT','')]


def cases():
    profile=dict(resource_id='opaque-alpha',user_id='subject',email=EMAIL)
    old=bundle(snapshot('account_service','customer_profiles',[profile],A))
    return [
        dict(name='deletion_then_sync_recreation',summary='An absence readback is followed by a new profile and enabled connector.',
             sources=[('old',A,old),('deleted',B,absence()),('recreated',C,bundle(snapshot('account_service','customer_profiles',[profile]),
                snapshot('integration_service','connector_configurations',[dict(resource_id='opaque-flow',customer_reference='subject',enabled=True)])))],
             expected=expected('deletion_then_sync_recreation','INCOMPLETE',[
                ('FINDING','account_service','opaque-alpha','REPORT_REMAINING_RECORD','PRESENT','recreated'),
                ('FINDING','integration_service','opaque-flow','REPORT_ACTIVE_DATA_FLOW','ACTIVE','recreated'),
                ('REMEDIATION','integration_service','opaque-flow','DISABLE_DATA_FLOW','DISABLED',''),
                ('REMEDIATION','account_service','opaque-alpha','DELETE_RECORD','ABSENT',''),
                ('VERIFICATION','account_service','opaque-alpha','VERIFY_RECORD_ABSENT','ABSENT',''),
                ('VERIFICATION','integration_service','opaque-flow','VERIFY_DATA_FLOW_DISABLED','DISABLED','')])),
        dict(name='clean_primary_dirty_replica',summary='The primary is absent; an independently captured SQL replica remains present.',
             sources=[('old',A,old),('deleted',B,absence()),('replica',C,
                '-- Captured at: '+C+'\nCREATE TABLE customer_profiles_failover_replica(resource_id TEXT,user_id TEXT,email TEXT);\n'
                "INSERT INTO customer_profiles_failover_replica VALUES ('opaque-alpha','subject','person@example.test');")],
             expected=expected('clean_primary_dirty_replica','INCOMPLETE',deletion_steps('replica'))),
        dict(name='retained_clean_tombstone',summary='Deleted tombstone keeps only the internal reference; all direct fields are explicitly null.',
             sources=[('cleanref',C,bundle(snapshot('identity_service','deleted_customer_references',[dict(
                resource_id='opaque-reference',subject_reference='subject',account_status='DELETED',name=None,email=None,phone=None,email_hash=None)])))],
             expected=expected('retained_clean_tombstone','COMPLETE',[
                ('VERIFICATION','identity_service','opaque-reference','VERIFY_RECORD_REDACTED','REDACTED','cleanref')])),
        dict(name='shared_support_personal_attachment',summary='Shared conversation is redacted; a personal attachment is removed.',
             sources=[('shared',C,bundle(snapshot('support_platform','support_conversations',[dict(resource_id='opaque-message',requester_email=EMAIL,message_text='Shared order context')]),
                 snapshot('support_platform','ticket_attachments',[dict(resource_id='opaque-attachment',uploaded_by=EMAIL,file_name='personal.pdf')])) )],
             expected=expected('shared_support_personal_attachment','INCOMPLETE',[
                ('FINDING','support_platform','opaque-attachment','REPORT_REMAINING_RECORD','PRESENT','shared'),
                ('FINDING','support_platform','opaque-message','REPORT_EXPOSED_PII','PRESENT','shared'),
                ('REMEDIATION','support_platform','opaque-attachment','DELETE_RECORD','ABSENT',''),
                ('REMEDIATION','support_platform','opaque-message','REDACT_RECORD','REDACTED',''),
                ('VERIFICATION','support_platform','opaque-attachment','VERIFY_RECORD_ABSENT','ABSENT',''),
                ('VERIFICATION','support_platform','opaque-message','VERIFY_RECORD_REDACTED','REDACTED','')])),
        dict(name='future_source_unavailable',summary='A clean observation is captured after cutoff and cannot erase the visible profile.',
             sources=[('old',A,old),('future',E,absence(B))],
             expected=expected('future_source_unavailable','INCOMPLETE',deletion_steps('old'))),
        dict(name='non_deletion_authorization',summary='An explicit request against deletion authorizes no destructive plan.',
             text='Do not delete my account',sources=[('old',A,old)],
             expected=expected('non_deletion_authorization','NOT_APPLICABLE',[])),
        dict(name='retention_and_blocked_restore',summary='Retained security and archive remain present, with restore disabled.',
             sources=[('retained',C,bundle(snapshot('security_service','security_audit_events',[dict(resource_id='opaque-audit',subject_reference='subject',retention_until='2030-01-01T00:00:00Z')]),
                 snapshot('backup_service','backup_catalog',[dict(resource_id='opaque-archive',subject_reference='subject',archive_key='archive/object.zst',restore_allowed=False)])))],
             expected=expected('retention_and_blocked_restore','COMPLETE',[
                 ('VERIFICATION','security_service','opaque-audit','VERIFY_RECORD_PRESENT','PRESENT','retained'),
                 ('VERIFICATION','backup_service','opaque-archive','VERIFY_RESTORE_BLOCKED','BLOCKED','retained')])),
    ]


def run_custom_cases(policy_rows):
    outcomes=[]
    for case in cases():
        with TemporaryDirectory() as directory:
            root=Path(directory)
            request=dict(case_id=case['name'],email=EMAIL,requested_at=A,reported_status='COMPLETE',
                         reported_at=B,audit_at=D,request_text=case.get('text','Delete my personal data'))
            data={'deletion_requests':[request], 'user_identities':[dict(user_id='subject',name='Independent Person',email=EMAIL,
                  phone='+15551234567',email_hash='independent-hash',updated_at=A)],'deletion_rules':policy_rows}
            for name,columns,_ in TABLE_SPECS:_csv(root/(name+'.csv'),columns,data.get(name,[]))
            manifest=[]
            for file_id,available,content in case['sources']:
                suffix='.sql' if isinstance(content,str) else '.json'
                path=root/(file_id+suffix);path.write_text(content if isinstance(content,str) else json.dumps(content),encoding='utf-8')
                manifest.append(dict(file_id=file_id,file_path=path.name,origin='independent fixture',captured_at=available))
            _csv(root/'evidence_files.csv',('file_id','file_path','origin','captured_at'),manifest)
            context=prepare(root)
            try:
                result=audit_request(context,request);actual=to_csv_row(result)
                passed=actual==case['expected']
                mutation_rejected=True
                if result.steps:
                    forged=replace(result,steps=(replace(result.steps[0],evidence_id='invented'),*result.steps[1:]))
                    mutation_rejected=not verify_result(forged,request,context.identities,context.states,policy_rows).valid
                outcomes.append(dict(name=case['name'],summary=case['summary'],passed=passed,
                    expected=case['expected'],actual=actual,mutation_rejected=mutation_rejected))
            except ValueError as exc:
                outcomes.append(dict(name=case['name'],summary=case['summary'],passed=False,expected=case['expected'],
                                     actual=None,error=str(exc),mutation_rejected=None))
    return outcomes
