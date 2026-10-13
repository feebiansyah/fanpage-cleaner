"""Versioned allowlist ledger. Recovered jobs are never executable or owner-bound."""
import json
import os
import re
import time
import uuid
from pathlib import Path

JOB_KEYS={'version','job_id','owner_id','created_at','state','mode','targets'}
TARGET_KEYS={'page_id','key','object_id','canonical','delete_target','operation','eligible','reasons','status','attempts'}
STATES={'queued','processing','dry_run_checked','succeeded','failed','uncertain','cancelled'}


class Checkpoints:
    def __init__(self,root,settings):
        self.root=Path(root)
        self.settings=settings
        self.generations={}

    def _files(self):
        if self.root.resolve()!=self.root.absolute():
            raise ValueError('unsafe checkpoint directory')
        self.root.mkdir(parents=True,exist_ok=True)
        if self.root.is_symlink():
            raise ValueError('unsafe checkpoint root')
        files=[]
        for path in self.root.glob('*.json'):
            if re.fullmatch('[a-f0-9]{32}\\.json',path.name) and not path.is_symlink():
                if time.time()-path.stat().st_mtime>self.settings.checkpoint_retention:
                    path.unlink()
                    self.generations.pop(path.stem,None)
                    for shard in self.root.glob(path.stem+'.*.json'):
                        if re.fullmatch('[a-f0-9]{32}\\.[0-9]{5}\\.json',shard.name) and not shard.is_symlink():
                            shard.unlink()
                else: files.append(path)
        for temporary in self.root.glob('*.tmp'):
            if re.fullmatch('[a-f0-9]{32}\\.tmp',temporary.name) and not temporary.is_symlink() and time.time()-temporary.stat().st_mtime>self.settings.checkpoint_retention:
                temporary.unlink()
        return files

    def _validate(self,record):
        if not isinstance(record,dict) or set(record) not in (JOB_KEYS,JOB_KEYS|{'generation'}) or type(record.get('version')) is not int or record.get('version')!=1 or record.get('mode')!='dry_run':
            raise ValueError('invalid checkpoint')
        if 'generation' in record and (not isinstance(record['generation'],str) or not re.fullmatch('[a-f0-9]{32}',record['generation'])):
            raise ValueError('invalid generation')
        if not all(isinstance(record.get(k),str) and re.fullmatch('[a-f0-9]{32}',record[k]) for k in ('job_id','owner_id')):
            raise ValueError('invalid checkpoint identity')
        if record.get('state') not in {'queued','running','completed','cancelled','failed','paused_needs_reconnect'}:
            raise ValueError('invalid state')
        if type(record.get('created_at')) not in {int,float} or not 0<record['created_at']<=time.time()+60:
            raise ValueError('invalid creation time')
        targets=record.get('targets')
        if not isinstance(targets,list) or len(targets)>self.settings.dry_max_targets:
            raise ValueError('invalid targets')
        for row in targets:
            if not isinstance(row,dict) or set(row)!=TARGET_KEYS or row.get('status') not in STATES:
                raise ValueError('invalid target')
            if not isinstance(row['page_id'],str) or not re.fullmatch('[0-9]{1,40}',row['page_id']):
                raise ValueError('invalid Page')
            if not isinstance(row['object_id'],str) or not re.fullmatch('[0-9]{1,40}(?:_[0-9]{1,40})?',row['object_id']):
                raise ValueError('invalid object')
            if row['delete_target'] is not None and not re.fullmatch('[0-9]{1,40}(?:_[0-9]{1,40})?',row['delete_target']):
                raise ValueError('invalid proposed target')
            if not re.fullmatch('(published_posts|video_reels):[0-9]{1,40}(?:_[0-9]{1,40})?',row['key']) or not re.fullmatch('(post|video):[0-9]{1,40}(?:_[0-9]{1,40})?',row['canonical']):
                raise ValueError('invalid canonical')
            if row['operation'] not in {'post_node','unverified'} or type(row['eligible']) is not bool or type(row['attempts']) is not int or not 0<=row['attempts']<=3:
                raise ValueError('invalid target policy')
            if not isinstance(row['reasons'],list) or len(row['reasons'])>12 or any(not isinstance(r,str) or not re.fullmatch('[a-z_]{1,80}',r) for r in row['reasons']):
                raise ValueError('invalid reason')

    def write(self,record):
        self._validate(record)
        generation=uuid.uuid4().hex
        record={**record,'generation':generation}
        data=json.dumps(record,ensure_ascii=True,separators=(',',':')).encode()
        files=self._files()
        target=self.root/(record['job_id']+'.json')
        if target.is_symlink() or len(data)>self.settings.checkpoint_max_file_bytes or (not target.exists() and len(files)>=self.settings.checkpoint_max_files):
            raise ValueError('checkpoint capacity/path')
        self._atomic(target,data)
        self.generations[record['job_id']]=generation

    def _atomic(self,target,data):
        if target.is_symlink(): raise ValueError('unsafe target')
        files=[p for p in self.root.iterdir() if p.is_file() and not p.is_symlink() and p.suffix in {'.json','.tmp'}]
        if len(files)>self.settings.checkpoint_max_files*(1+(self.settings.dry_max_targets+255)//256):
            raise ValueError('checkpoint file capacity')
        # Include staging and abandoned tmp files, not just final ledger bytes.
        total=sum(p.stat().st_size for p in files)+len(data)
        if total>self.settings.checkpoint_max_bytes:
            raise ValueError('checkpoint capacity')
        temporary=self.root/(uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary,target)
        finally:
            if temporary.exists(): temporary.unlink()

    def write_progress(self,record,chunk):
        self._validate(record)
        if type(chunk) is not int or not 0<=chunk<(self.settings.dry_max_targets+255)//256 or len(record['targets'])>256:
            raise ValueError('invalid progress chunk')
        generation=self.generations.get(record['job_id'])
        if not generation: raise ValueError('missing manifest')
        data=json.dumps({'version':1,'base_generation':generation,'record':record},separators=(',',':')).encode()
        if len(data)>self.settings.checkpoint_max_file_bytes: raise ValueError('progress capacity')
        self._atomic(self.root/(record['job_id']+'.'+str(chunk).zfill(5)+'.json'),data)

    def restore(self):
        restored=[]
        total_bytes=0
        total_targets=0
        for path in self._files()[:self.settings.checkpoint_max_files]:
            if path.stat().st_size>self.settings.checkpoint_max_file_bytes:
                continue
            total_bytes+=path.stat().st_size
            if total_bytes>self.settings.checkpoint_max_bytes: break
            try:
                record=json.loads(path.read_bytes())
                self._validate(record)
                if path.stem!=record['job_id']: continue
            except (ValueError,TypeError,OSError,RecursionError): continue
            record['state']='paused_needs_reconnect'
            total_targets+=len(record['targets'])
            if total_targets>self.settings.dry_max_targets: break
            for chunk in range((len(record['targets'])+255)//256):
                shard=self.root/(path.stem+'.'+str(chunk).zfill(5)+'.json')
                if not shard.exists() or shard.is_symlink() or shard.stat().st_size>self.settings.checkpoint_max_file_bytes:
                    continue
                total_bytes+=shard.stat().st_size
                if total_bytes>self.settings.checkpoint_max_bytes:
                    return restored
                try:
                    delta=json.loads(shard.read_bytes())
                    if set(delta)!={'version','base_generation','record'} or type(delta['version']) is not int or delta['version']!=1 or delta['base_generation']!=record.get('generation'):
                        continue
                    part=delta['record']
                    self._validate(part)
                    if part['job_id']!=record['job_id'] or part['owner_id']!=record['owner_id'] or len(part['targets'])>256:
                        continue
                    offset=chunk*256
                    base=record['targets'][offset:offset+256]
                    immutable=lambda row:{k:v for k,v in row.items() if k not in {'status','attempts','reasons'}}
                    if len(base)!=len(part['targets']) or any(immutable(a)!=immutable(b) for a,b in zip(base,part['targets'])):
                        continue
                    record['targets'][offset:offset+256]=part['targets']
                except (ValueError,TypeError,OSError,KeyError,RecursionError): continue
            for row in record['targets']:
                if row['status']=='processing':
                    row['status']='uncertain'
                    row['reasons']=list(dict.fromkeys(row['reasons']+['interrupted_processing']))
            restored.append(record)
        return restored
