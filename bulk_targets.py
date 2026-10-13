"""Trusted audit metadata -> identities. No user supplied delete identifiers."""
from dataclasses import dataclass, field
from content import identifier
from errors import AppError


def identity_locks(canonical, details):
    """Known provenance aliases are locks, never alternate DELETE targets."""
    locks={canonical}
    for video_id in details.get('related_video_ids',[]):
        if identifier(video_id):
            locks.add('video:'+video_id)
    linked=details.get('linked_post_id')
    if identifier(linked): locks.add('post:'+linked)
    return locks


def suppression_ids(target_id, details):
    """Suppress proven audit aliases, without claiming their nodes were deleted."""
    ids={target_id} if identifier(target_id) else set()
    ids.update(node for node in details.get('related_video_ids',[]) if identifier(node))
    if details.get('operation_scope')=='reel_video' and identifier(details.get('linked_post_id')):
        ids.add(details['linked_post_id'])
    return ids


def suppression_reservations(page_id,target_id,details):
    return {page_id+':'+node for node in suppression_ids(target_id,details)}


@dataclass
class Target:
    page_id: str
    key: str
    object_id: str
    canonical: str
    delete_target: str | None
    operation: str
    eligible: bool
    reasons: list[str]
    status: str = 'queued'
    attempts: int = 0
    retry_at: float = 0
    details: dict = field(default_factory=dict,repr=False)
    diagnostic: dict = field(default_factory=dict,repr=False)
    cleanup_warning: str | None = None
    scope: str = field(default='',repr=False)

    def public(self):
        return {key:getattr(self,key) for key in ('page_id','key','object_id','canonical','delete_target','operation','eligible','reasons','status','attempts')}


def from_audit(item, page_id):
    raw=identifier(item.get('object_id'))
    sources=item.get('sources',[])
    if not raw or item.get('page_id') != page_id or not isinstance(sources,list):
        raise AppError('invalid_selection')
    posts=[s['object_id'] for s in sources if isinstance(s,dict) and s.get('source')=='published_posts' and identifier(s.get('object_id'))]
    videos=[s['object_id'] for s in sources if isinstance(s,dict) and s.get('source')=='video_reels' and identifier(s.get('object_id'))]
    links={s['post_id'] for s in sources if isinstance(s,dict) and s.get('source')=='video_reels' and identifier(s.get('post_id'))}
    if not posts and not videos:
        raise AppError('invalid_selection')
    unique_posts=set(posts)
    reasons=['story_exclusion_unverified','delete_permissions_unverified','success_semantics_unverified']
    if len(unique_posts)==1:
        node=posts[0]
        canonical='post:'+node
        proposed=node  # SDK node operation, exact original ID, never synthesized.
        operation='post_node'
    else:
        canonical='post:'+next(iter(links)) if len(links)==1 else 'video:'+raw
        proposed=None
        operation='unverified'
        reasons.append('reel_delete_semantics_unverified' if not posts else 'ambiguous_post_identity')
    if item.get('kind')=='Story':
        reasons.insert(0,'identified_story')
        proposed=None
        operation='unverified'
    # No production proof of complete Story exclusion / permissions exists yet.
    return Target(page_id,item['key'],raw,canonical,proposed,operation,False,reasons)


def validate_selections(store,browser_id,session_id,payload):
    if not isinstance(payload,dict) or set(payload)!={'selections'}:
        raise AppError('invalid_selection')
    groups=payload['selections']
    if not isinstance(groups,list) or not groups or len(groups)>200:
        raise AppError('invalid_selection')
    targets,duplicates,seen,pages=[],0,set(),{}
    selected_count=0
    for group in groups:
        if not isinstance(group,dict) or set(group)!={'page_id','keys'} or not identifier(group.get('page_id')) or '_' in group['page_id']:
            raise AppError('invalid_selection')
        session,page=store.require_page(browser_id,session_id,group['page_id'])
        if not page.access_token:
            raise AppError('page_token_missing',403)
        keys=group['keys']
        if not isinstance(keys,list) or not keys or len(keys)>5000 or any(not isinstance(k,str) or len(k)>160 for k in keys):
            raise AppError('invalid_selection')
        selected_count+=len(keys)
        if selected_count>store.settings.dry_max_targets:
            raise AppError('dry_capacity',503)
        audit=store.audits.get(session_id,page.id)
        if audit is None:
            raise AppError('audit_expired',409)
        index={item['key']:item for item in audit['items']}
        pages[page.id]=next(meta['name'] for meta in session.metadata() if meta['id']==page.id)
        for key in keys:
            if key not in index:
                raise AppError('invalid_selection')
            target=from_audit(index[key],page.id)
            if target.canonical in seen:
                duplicates+=1
            else:
                targets.append(target)
                seen.add(target.canonical)
            if len(targets)>store.settings.dry_max_targets:
                raise AppError('dry_capacity',503)
    if any(store.audits.get(session_id,pid) is None for pid in pages):
        raise AppError('audit_expired',409)
    return targets,duplicates,pages
