"""Short-lived confirmations for concrete supported Page Post subsets. Uses existing queue."""
import copy
import secrets
import time
from dataclasses import dataclass,field

from bulk_targets import Target
from content import identifier
from errors import AppError, MESSAGES
from single_delete import CONFIRMATION_TTL, MAX_CONFIRMATIONS, MAX_PER_SESSION


@dataclass
class BulkConfirmation:
    browser_id: str = field(repr=False)
    session_id: str = field(repr=False)
    expires_at: float
    targets: list
    pages: dict
    duplicates: int
    unsupported: int = 0
    job_id: str | None = None


class BulkDelete:
    def __init__(self,store,queue,single):
        self.store,self.queue,self.single=store,queue,single
        self.confirmations={}

    def cleanup(self):
        for cid,entry in list(self.confirmations.items()):
            if (entry.session_id not in self.store.sessions
                or (entry.job_id is None and entry.expires_at<=time.monotonic())
                or (entry.job_id is not None and entry.job_id not in self.queue.jobs)):
                del self.confirmations[cid]

    def prepare(self,browser_id,sid,payload):
        if not isinstance(payload,dict) or set(payload)!={'selections'} or not isinstance(payload['selections'],list) or not payload['selections']:
            raise AppError('invalid_selection')
        groups=payload['selections']
        total=0
        for group in groups:
            if (not isinstance(group,dict) or set(group)!={'page_id','keys'} or not identifier(group.get('page_id'))
                or '_' in group['page_id'] or not isinstance(group['keys'],list) or not group['keys']
                or any(not isinstance(k,str) or not k or len(k)>160 for k in group['keys'])):
                raise AppError('invalid_selection')
            total+=len(group['keys'])
        if total>self.store.settings.bulk_delete_max_targets: raise AppError('bulk_limit')
        self.cleanup()
        session=self.store.session(browser_id,sid)
        if session is None: raise AppError('session_expired',401)
        if len(self.confirmations)>=MAX_CONFIRMATIONS or sum(c.session_id==sid and c.job_id is None for c in self.confirmations.values())>=MAX_PER_SESSION:
            raise AppError('delete_capacity',503)
        items,targets,pages,seen=[],[],{},set()
        duplicates=0
        selected=set()
        for group in groups:
            session,page=self.store.require_page(browser_id,sid,group['page_id'])
            audit=self.store.audits.get(sid,page.id)
            if audit is None: raise AppError('audit_expired',409)
            index={i['key']:i for i in audit['items']}
            name=next(p['name'] for p in session.metadata() if p['id']==page.id)
            for key in group['keys']:
                if key not in index: raise AppError('invalid_selection')
                if (page.id,key) in selected:
                    duplicates+=1
                    continue
                selected.add((page.id,key))
                item=index[key]
                try:
                    _,_,detail,_=self.single.resolve(browser_id,sid,page.id,key)
                except AppError as error:
                    items.append({'supported':False,'page_id':page.id,'page_name':name,'key':key,
                        'target_id':None,'kind':item.get('kind'),'text':item.get('text',''),
                        'permalink':item.get('permalink'),'reason':error.code,'message':MESSAGES[error.code]})
                    continue
                canonical='post:'+detail['target_id']
                if canonical in seen:
                    duplicates+=1
                    continue
                seen.add(canonical)
                pages[page.id]=name
                target=Target(page.id,key,item['object_id'],canonical,detail['target_id'],'post_node',True,[],details=copy.deepcopy(detail),scope=self.store.token_key(page.access_token))
                targets.append(target)
                items.append({**detail,'supported':True,'reason':None,'message':'Target Page Post; objek media terpisah tidak dijamin ikut dihapus.'})
        cid=secrets.token_urlsafe(32)
        expires=min(session.expires_at,time.monotonic()+CONFIRMATION_TTL)
        unsupported=sum(not i['supported'] for i in items)
        self.confirmations[cid]=BulkConfirmation(browser_id,sid,expires,targets,pages,duplicates,unsupported)
        return {'confirmation_id':cid,'expires_in_seconds':max(0,int(expires-time.monotonic())),
            'selected':total,'supported':len(targets),'unsupported':sum(not i['supported'] for i in items),
            'duplicates':duplicates,'items':items,'max_targets':self.store.settings.bulk_delete_max_targets,
            'story_automatically_verified':False,'message':'Konfirmasi hanya subset didukung yang ditampilkan. Periksa daftar/permalink secara manual, bukan Story. Penghapusan Page Post tidak dapat dibatalkan.'}

    def confirm(self,browser_id,sid,payload):
        if (not isinstance(payload,dict) or set(payload)!={'confirmation_id','manual_targets_confirmed'}
            or not isinstance(payload['confirmation_id'],str) or len(payload['confirmation_id'])>100
            or payload['manual_targets_confirmed'] is not True): raise AppError('invalid_selection')
        session=self.store.session(browser_id,sid)
        if session is None: raise AppError('session_expired',401)
        entry=self.confirmations.get(payload['confirmation_id'])
        if entry is None or entry.browser_id!=browser_id or entry.session_id!=sid: raise AppError('bulk_confirmation_not_found',404)
        if entry.job_id:
            return self.queue.snapshot(self.owned(browser_id,sid,entry.job_id))
        if entry.expires_at<=time.monotonic(): raise AppError('confirmation_expired',409)
        if not entry.targets: raise AppError('bulk_no_supported_targets',409)
        for target in entry.targets:
            _,page=self.store.require_page(browser_id,sid,target.page_id)
            if not page.access_token: raise AppError('page_token_missing',403)
            target.scope=self.store.token_key(page.access_token)
        # Atomic enqueue + consumption, without await. No cache lookup: confirmed snapshot owns IDs.
        job=self.queue.add(browser_id,sid,copy.deepcopy(entry.targets),entry.duplicates,dict(entry.pages),'delete_page_posts',entry.unsupported)
        entry.job_id=job.id
        return self.queue.snapshot(job)

    def owned(self,browser_id,sid,job_id):
        try: job=self.queue.owned(browser_id,sid,job_id)
        except AppError: raise AppError('bulk_job_not_found',404) from None
        if job.mode!='delete_page_posts': raise AppError('bulk_job_not_found',404)
        return job

    def active(self,browser_id,sid):
        if self.store.session(browser_id,sid) is None: raise AppError('session_expired',401)
        jobs=[job for job in self.queue.jobs.values() if job.mode=='delete_page_posts' and job.browser_id==browser_id and job.session_id==sid]
        jobs.sort(key=lambda j:j.created_at,reverse=True)
        return {'jobs':[self.queue.snapshot(j) for j in jobs if not j.terminal],
                'last_job':self.queue.snapshot(jobs[0]) if jobs else None}
