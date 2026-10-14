"""Bounded GET-only probes of a failed result; not a delete eligibility check."""
import asyncio
import re
import time
from errors import AppError
from graph_api import sanitized_meta_message
from content import safe_url

PERMISSIONS=('pages_manage_posts','pages_read_engagement','pages_show_list','pages_read_user_content','pages_manage_metadata','business_management')
PROBES=(
    ('permissions','me/permissions',{'fields':'permission,status','limit':'100'},'user'),
    ('page_tasks','me/accounts',{'fields':'id,tasks','limit':'100'},'user'),
    ('post_identity','{id}',{'fields':'id,from,status_type'},'page'),
    ('post_media','{id}',{'fields':'id,application,object_id'},'page'),
    ('attachments','{id}/attachments',{'fields':'type,target,subattachments.limit(5){type,target}','limit':'5'},'page'),
)

def object_id(value):
    return value if isinstance(value,str) and re.fullmatch(r'[0-9]{1,40}(?:_[0-9]{1,40})?',value) else None

class FailureDiagnosis:
    def __init__(self,store,bulk,single):
        self.store,self.bulk,self.single=store,bulk,single
        self.active=set()

    def audit_target(self,browser,sid,payload):
        if (not isinstance(payload.get('page_id'),str) or not re.fullmatch('[0-9]{1,40}',payload['page_id'])
            or not isinstance(payload.get('keys'),list) or len(payload['keys'])!=1
            or not isinstance(payload['keys'][0],str) or not 0<len(payload['keys'][0])<=160):
            raise AppError('invalid_selection')
        session,page=self.store.require_page(browser,sid,payload['page_id'])
        if not page.access_token: raise AppError('page_token_missing',403)
        audit=self.store.audits.get(sid,page.id)
        if audit is None: raise AppError('audit_expired',409)
        matches=[item for item in audit['items'] if item.get('key')==payload['keys'][0]]
        if not matches: raise AppError('invalid_selection')
        if len(matches)!=1: raise AppError('post_identity_ambiguous',409)
        item=matches[0]
        if item.get('page_id')!=page.id: raise AppError('page_not_owned',403)
        sources=item.get('sources')
        if item.get('kind')=='Story' or item.get('identified_story') is True:
            raise AppError('identified_story',409)
        if not isinstance(sources,list) or any(not isinstance(s,dict) for s in sources):
            raise AppError('post_identity_ambiguous',409)
        if any(s.get('source')=='stories' for s in sources): raise AppError('identified_story',409)
        if any(s.get('source') not in {'published_posts','video_reels'} or not object_id(s.get('object_id')) for s in sources):
            raise AppError('post_identity_ambiguous',409)
        nodes={s['object_id'] for s in sources if s['source']=='published_posts'}
        if not nodes: raise AppError('page_post_missing',409)
        if len(nodes)!=1: raise AppError('post_identity_ambiguous',409)
        node=next(iter(nodes))
        if item.get('object_id') not in {s['object_id'] for s in sources} or any(s.get('post_id')!=node for s in sources if s['source']=='video_reels'):
            raise AppError('post_identity_ambiguous',409)
        ids={s['object_id'] for s in sources}|{node}
        for job in self.store.content_jobs.entries.values():
            if job.page_id==page.id and ids.intersection(job.story_ids): raise AppError('identified_story',409)
        tokens=self.store.known_tokens()
        clean=lambda value:sanitized_meta_message(value,tokens)
        target={'page_id':page.id,'key':item['key'],'target_id':node,'operation_scope':'page_post',
                'page_name':next(p['name'] for p in session.metadata() if p['id']==page.id),
                'kind':clean(item.get('kind')),'text':clean(item.get('text')),
                'permalink':safe_url(item.get('permalink'),False,list(tokens))}
        identity=(page.id,node,tuple(sorted((s['source'],s['object_id'],s.get('post_id') or '') for s in sources)))
        return target,identity

    def target(self,browser,sid,payload):
        if self.store.session(browser,sid) is None: raise AppError('session_expired',401)
        if isinstance(payload,dict) and set(payload)=={'page_id','keys'}:
            return self.audit_target(browser,sid,payload)[0],{}
        if isinstance(payload,dict) and set(payload)=={'job_id','key'}:
            if not all(isinstance(v,str) and 0<len(v)<=160 for v in payload.values()):
                raise AppError('invalid_selection')
            try: job=self.bulk.owned(browser,sid,payload['job_id'])
            except AppError as error:
                if error.status==404: raise AppError('diagnosis_unavailable',404) from None
                raise
            matches=[t for t in job.targets if t.key==payload['key']]
            if len(matches)!=1: raise AppError('invalid_selection')
            target=matches[0]
            if target.status!='failed' or target.details.get('operation_scope')!='page_post':
                raise AppError('diagnosis_not_failed_post',409)
            return dict(target.details),dict(target.diagnostic)
        if isinstance(payload,dict) and set(payload)=={'last_result'} and payload['last_result'] is True:
            record=self.single.last(browser,sid)
            result=record.get('result',{})
            if not result: raise AppError('diagnosis_unavailable',404)
            target=result.get('target',{})
            if result.get('outcome')!='failed' or target.get('operation_scope')!='page_post':
                raise AppError('diagnosis_not_failed_post',409)
            return dict(target),dict(result.get('diagnostic',{}))
        raise AppError('invalid_selection')

    async def run(self,graph,browser,sid,payload):
        target,previous=self.target(browser,sid,payload)
        current_audit=set(payload)=={'page_id','keys'}
        audit_identity=self.audit_target(browser,sid,payload)[1] if current_audit else None
        page_id,node=target['page_id'],target['target_id']
        if not object_id(node): raise AppError('post_identity_ambiguous',409)
        session,page=self.store.require_page(browser,sid,page_id)
        if not page.access_token: raise AppError('page_token_missing',403)
        if sid in self.active or len(self.active)>=self.store.settings.max_jobs:
            raise AppError('diagnosis_busy',409)
        self.active.add(sid)
        user_scope=self.store.token_key(session.user_token)
        page_scope=self.store.token_key(page.access_token)
        deadline=min(session.expires_at,time.monotonic()+self.store.settings.operation_timeout)
        del session,page
        report={'target':target,'request_count':0,'request_budget':5,'checks':[],
            'permissions':{p:'unknown' for p in PERMISSIONS},'tasks':None,'owner':None,
            'post_id':None,'status_type':None,'application':None,'media_object_id':None,
            'photo_ids':[],'relations':[],'delete_permission_proven':False,
            'connection_context':'current_session','retry_after':None,
            'diagnosis_origin':'current_audit' if current_audit else 'failed_result',
            'previous_failure':{k:previous.get(k) for k in ('http_status','error_code','error_subcode','reason')},
            'message':'GET hanya memeriksa koneksi sesi saat ini: sukses GET tidak membuktikan izin DELETE, gagal GET tidak membuktikan objek terhapus. Setelah reconnect/restart, keadaan token lama tidak dapat dibuktikan.'}
        def before_send():
            live,p=self.store.require_page(browser,sid,page_id)
            if not p.access_token: raise AppError('page_token_missing',403)
            if self.store.token_key(p.access_token)!=page_scope or self.store.token_key(live.user_token)!=user_scope:
                raise AppError('session_expired',401)
            if current_audit and self.audit_target(browser,sid,payload)[1]!=audit_identity:
                raise AppError('post_identity_ambiguous',409)
            self.store.check_cooldown(page_scope)
            self.store.check_cooldown(user_scope)
            if report['request_count']>=5: raise AppError('diagnosis_budget',409)
            report['request_count']+=1
        def clean(value):
            return sanitized_meta_message(value,self.store.known_tokens())
        try:
            for source,path,params,credential in PROBES:
                check={'source':source,'requested_fields':params['fields'],'status':'unknown','reason':None}
                observation={}
                try:
                    live,p=self.store.require_page(browser,sid,page_id)
                    if not p.access_token: raise AppError('page_token_missing',403)
                    self.store.check_cooldown(page_scope)
                    self.store.check_cooldown(user_scope)
                    if report['request_count']>=5: raise AppError('diagnosis_budget',409)
                    token=live.user_token if credential=='user' else p.access_token
                    scope=user_scope if credential=='user' else page_scope
                    endpoint='https://graph.facebook.com/'+self.store.settings.graph_version+'/'+path.format(id=node)
                    data=await asyncio.wait_for(graph._get(token,scope,endpoint,params,deadline,before_send,observation),max(.001,deadline-time.monotonic()))
                    del token,live,p
                    check.update(status='received',reason='metadata_received',**observation)
                    if source=='permissions':
                        if not isinstance(data.get('data'),list): check.update(status='unknown',reason='response_invalid')
                        else:
                            for row in data['data'][:100]:
                                permission=row.get('permission') if isinstance(row,dict) else None
                                if isinstance(permission,str) and re.fullmatch('[a-z][a-z0-9_]{0,79}',permission) and row.get('status') in {'granted','declined','expired'}:
                                    report['permissions'][clean(permission)]=row['status']
                        check['truncated']=bool(data.get('paging',{}).get('next')) if isinstance(data.get('paging'),dict) else False
                    elif source=='page_tasks':
                        rows=data.get('data')
                        matches=[r for r in rows[:100] if isinstance(r,dict) and r.get('id')==page_id] if isinstance(rows,list) else []
                        if len(matches)==1 and isinstance(matches[0].get('tasks'),list):
                            report['tasks']=[clean(task) for task in matches[0]['tasks'][:50] if isinstance(task,str) and re.fullmatch('[A-Z_]{1,80}',task)]
                        else: check.update(status='unknown',reason='page_tasks_not_available')
                        check['truncated']=bool(data.get('paging',{}).get('next')) if isinstance(data.get('paging'),dict) else False
                    elif source in {'post_identity','post_media'}:
                        if data.get('id')!=node:
                            check.update(status='unknown',reason='object_identity_unverified')
                        elif source=='post_identity':
                            report['post_id']=node
                            owner=data.get('from')
                            if isinstance(owner,dict) and object_id(owner.get('id')):
                                report['owner']={'id':owner['id'],'name':clean(owner.get('name')),'matches_page':owner['id']==page_id}
                            report['status_type']=clean(data.get('status_type'))
                            check['unavailable_fields']=[k for k in ('from','status_type') if k not in data]
                        else:
                            app=data.get('application')
                            if isinstance(app,dict) and object_id(app.get('id')):
                                report['application']={'id':app['id'],'name':clean(app.get('name'))}
                            report['media_object_id']=object_id(data.get('object_id'))
                            check['unavailable_fields']=[k for k in ('application','object_id') if k not in data]
                    else:
                        rows=data.get('data')
                        if not isinstance(rows,list): check.update(status='unknown',reason='response_invalid')
                        else:
                            def relation(row):
                                if not isinstance(row,dict): return
                                media=row.get('target')
                                media_id=object_id(media.get('id')) if isinstance(media,dict) else None
                                kind=clean(row.get('type'))
                                if media_id:
                                    report['relations'].append({'source':'attachments.target','type':kind,'id':media_id})
                                    if row.get('type')=='photo' and media_id not in report['photo_ids']: report['photo_ids'].append(media_id)
                            for row in rows[:5]:
                                relation(row)
                                sub=row.get('subattachments') if isinstance(row,dict) else None
                                if isinstance(sub,dict) and isinstance(sub.get('data'),list):
                                    for child in sub['data'][:5]: relation(child)
                                    if isinstance(sub.get('paging'),dict) and sub['paging'].get('next'): check['truncated']=True
                        check['truncated']=check.get('truncated',False) or (bool(data.get('paging',{}).get('next')) if isinstance(data.get('paging'),dict) else False)
                except AppError as error:
                    check.update(status='unavailable',reason=error.code,**observation)
                    if error.retry_after: report['retry_after']=max(report['retry_after'] or 0,error.retry_after)
                    if observation.get('error_code')==100:
                        # Only names explicitly mentioned by Meta, not a guessed failing field.
                        check['failed_fields']=[field for field in ('id','from','status_type','application','object_id','type','target','subattachments','tasks','permission','status') if re.search(r'\b'+field+r'\b',observation.get('error_message') or '')]
                        if not check['failed_fields']: check['failed_fields']=['field_group_unknown']
                except asyncio.TimeoutError:
                    check.update(status='unavailable',reason='timeout',**observation)
                except Exception:
                    check.update(status='unavailable',reason='diagnosis_internal_error')
                report['checks'].append(check)
            # Never return old browser results after disconnect/expiry during the probe.
            self.store.require_page(browser,sid,page_id)
            if current_audit and self.audit_target(browser,sid,payload)[1]!=audit_identity:
                raise AppError('post_identity_ambiguous',409)
            return report
        finally:
            self.active.discard(sid)
