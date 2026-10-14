"""Conservative classification shared by audit loading and deletion eligibility."""

SCOPE_VERSION = 1

import re

def attachment_metadata(value, depth=0):
    """Retain classification facts only, never upstream URLs or tokens."""
    if depth > 8 or not isinstance(value, dict) or not isinstance(value.get('data'), list):
        return None
    result = {'data': []}
    paging = value.get('paging')
    if paging is not None and not isinstance(paging, dict):
        return None
    if paging and paging.get('next'):
        result['paging'] = {'next': True}
    for child in value['data']:
        if not isinstance(child, dict):
            return None
        entry = {'type': child.get('type') if isinstance(child.get('type'), str) else None}
        if 'subattachments' in child:
            entry['subattachments'] = attachment_metadata(child['subattachments'], depth + 1)
        result['data'].append(entry)
    return result

def scope_decision(item):
    if not isinstance(item, dict):
        return 'unknown_scope'
    object_id, page_id = item.get('object_id'), item.get('page_id')
    if (not isinstance(object_id, str) or not isinstance(page_id, str)
            or not re.fullmatch(r'[0-9]{1,40}(?:_[0-9]{1,40})?', object_id)
            or ('_' in object_id and object_id.split('_')[0] != page_id)):
        return 'post_identity_ambiguous'
    if item.get('identified_story'):
        return 'identified_story'
    sources = item.get('sources')
    if (not isinstance(sources, list) or len(sources) != 1
            or not isinstance(sources[0], dict)
            or sources[0].get('source') != 'published_posts'
            or sources[0].get('object_id') != item.get('object_id')
            or sources[0].get('post_id')):
        return 'ambiguous_provenance'
    kind = item.get('kind')
    if not isinstance(kind, str):
        return 'unknown_scope'
    if kind in {'Video', 'Reel', 'Story'}:
        return 'unsupported_scope'
    metadata = item.get('scope_metadata')
    if metadata is None:
        return 'unknown_scope'
    if not isinstance(metadata, dict):
        return 'unknown_scope'
    status = metadata.get('status_type')
    if not isinstance(status, str) or status not in {'mobile_status_update', 'wall_post', 'added_photos', 'shared_story'}:
        return 'unsupported_status_type'
    types = []
    def inspect(connection, depth=0):
        if depth > 8 or not isinstance(connection, dict) or not isinstance(connection.get('data'), list):
            return 'unknown_attachments'
        if not isinstance(connection.get('paging', {}), dict):
            return 'unknown_attachments'
        if connection.get('paging', {}).get('next'):
            return 'truncated_attachments'
        for attachment in connection['data']:
            if not isinstance(attachment, dict):
                return 'unknown_attachments'
            kind = attachment.get('type')
            if not isinstance(kind, str):
                return 'unknown_attachment_type'
            if kind not in {'photo', 'album', 'link', 'share'}:
                return 'unknown_attachment_type' if kind is None else 'unsupported_attachment_type'
            types.append(kind)
            children = attachment.get('subattachments')
            if kind == 'album' and children is None:
                return 'unknown_attachments'
            if kind == 'album' and isinstance(children, dict) and not children.get('data'):
                return 'unknown_attachments'
            if children is not None:
                reason = inspect(children, depth + 1)
                if reason:
                    return reason
        return None
    reason = inspect(metadata.get('attachments'))
    if reason:
        return reason
    if status == 'added_photos' and not any(t in {'photo', 'album'} for t in types):
        return 'unknown_attachments'
    if status == 'shared_story' and not types:
        return 'unknown_attachments'
    return None
