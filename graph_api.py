"""Shared bounded transport. Only sanitized, bounded DELETE error messages may be exposed."""

import asyncio
import hashlib
import json
import math
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit, quote, quote_plus
from contextlib import asynccontextmanager

import httpx

from config import Settings
from errors import AppError
from sessions import MemoryStore, Page


def sanitized_meta_message(value, tokens):
    if not isinstance(value,str): return None
    if len(value)>8192: return '[Pesan Meta terlalu panjang; tidak ditampilkan]'
    # Redact before truncating so a long credential never becomes a leaked prefix.
    secrets=set()
    for token in tokens:
        if token: secrets.update((token,quote(token,safe=''),quote_plus(token)))
    for secret in sorted(secrets,key=len,reverse=True):
        value=value.replace(secret,'[disamarkan]')
    value=re.sub(r'(?i)(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>\"\']+', '[URL disamarkan]',value)
    value=re.sub(r'(?i)\b(?:access_token|authorization|token)\s*[:=]\s*(?:Bearer\s+)?[^\s,;]+','[credential disamarkan]',value)
    value=re.sub(r'(?i)\bBearer\s+[^\s,;]+','[credential disamarkan]',value)
    value=' '.join(''.join(c if c.isprintable() else ' ' for c in value).split())
    return value[:500] or None


def retry_after(value: str | None):
    if not value or len(value) > 128:
        return None
    try:
        if value.isascii() and value.isdigit():
            seconds = int(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = math.ceil((date - datetime.now(timezone.utc)).total_seconds())
        # Retain long valid instructions as cooldowns; never shorten them to retry sooner.
        return float(seconds) if seconds >= 0 and math.isfinite(float(seconds)) else None
    except (ValueError, OverflowError, TypeError):
        return None


def delete_internal_code(error, phase):
    # Static categories only: exception strings, repr, and class names may contain secrets.
    category = next((label for kind, label in (
        (KeyError,'key_error'), (TypeError,'type_error'), (RecursionError,'recursion_error'),
        (ValueError,'value_error'), (RuntimeError,'runtime_error'), (OSError,'os_error'),
        (AssertionError,'assertion_error')) if isinstance(error,kind)), 'unexpected_error')
    return phase+'.'+category


def json_value_type(value):
    if value is None: return 'null'
    if type(value) is bool: return 'boolean'
    if type(value) in {int,float}: return 'number'
    if isinstance(value,str): return 'string'
    if isinstance(value,dict): return 'object'
    if isinstance(value,list): return 'array'
    return 'unavailable'


class GraphClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings, store: MemoryStore):
        self.http = http
        self.settings = settings
        self.store = store

    async def delete_reel_once(self, target_id, token_key, deadline, token_at_dispatch, observation=None):
        """User-authorized ONE Reel experiment; existing strict criterion, not verified Reel contract."""
        if not isinstance(target_id,str) or not re.fullmatch(r'[0-9]{1,40}',target_id):
            raise AppError('reel_identity_ambiguous',409)
        return await self.delete_post_once(target_id,token_key,deadline,token_at_dispatch,observation)

    async def delete_post_once(self, target_id, token_key, deadline, token_at_dispatch, observation=None):
        """Strict v26 Page Post contract. One send; never calls the GET retry helper."""
        if not re.fullmatch(r'[0-9]{1,40}(?:_[0-9]{1,40})?', target_id):
            raise AppError('post_delete_unsupported', 409)
        observation = observation if observation is not None else {}
        observation.update(http_status=None,error_code=None,error_subcode=None,content_type=None,
                           json_parsed=None,json_type=None,success_type='unavailable',has_error_field=None,
                           response_issue=None,internal_code=None,phase='governor_admission',request_started=False)
        instruction = None
        token = ''
        known_tokens = self.store.known_tokens()
        def result(outcome, reason, wait=None):
            return {**observation, 'outcome':outcome, 'reason':reason, 'retry_after':wait}
        try:
          async with self.store.governor.slot(token_key, deadline):
            # Session/audit/confirmation may expire while waiting. Fetch token only now.
            observation['phase']='target_validation'
            endpoint = 'https://graph.facebook.com/v26.0/'+target_id
            try:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise AppError('confirmation_expired',409)
                async def send():
                    nonlocal instruction, token
                    token = await self.store.governor.pace_dispatch(token_key, deadline, token_at_dispatch)
                    observation.update(phase='request_dispatch',request_started=True)
                    async with self.http.stream('DELETE', endpoint, headers={'Authorization':'Bearer '+token}) as response:
                        status = response.status_code
                        observation.update(http_status=status,phase='response_headers')
                        mime=response.headers.get('content-type','').split(';',1)[0].strip().lower()
                        # Do not reflect arbitrary header values (or charset/parameters).
                        observation['content_type'] = (mime if mime in {'application/json','application/problem+json',
                            'text/html','text/plain','application/octet-stream'} else 'other' if mime else None)
                        self._usage(response.headers, token_key)
                        instruction = retry_after(response.headers.get('retry-after'))
                        if instruction is not None:
                            # Install before releasing the shared permit, even with invalid body.
                            self.store.cooldown(token_key,instruction,reason='retry_after',source='retry-after')
                        if status == 429:
                            self.store.cooldown(token_key,max(instruction or 0,self.settings.cooldown_seconds),reason='meta_http_429',source='http_status')
                        body = bytearray()
                        observation['phase']='response_body'
                        async for chunk in response.aiter_bytes():
                            if len(body)+len(chunk)>self.settings.max_response_bytes:
                                observation['response_issue']='response_too_large'
                                return None
                            body.extend(chunk)
                        try:
                            observation['phase']='response_parse'
                            payload=json.loads(body)
                            observation.update(json_parsed=True,json_type=json_value_type(payload))
                            if isinstance(payload,dict):
                                observation['success_type']=json_value_type(payload['success']) if 'success' in payload else 'missing'
                                observation['has_error_field']='error' in payload
                                error=payload.get('error')
                                if isinstance(error,dict):
                                    redactions=known_tokens | self.store.known_tokens() | {token}
                                    observation['error_message']=sanitized_meta_message(error.get('message'),redactions)
                                    observation['error_user_msg']=sanitized_meta_message(error.get('error_user_msg'),redactions)
                            return payload
                        except (ValueError,UnicodeError,RecursionError):
                            observation.update(json_parsed=False,response_issue='json_parse_failed' if body else 'empty_body')
                            return None
                payload = await asyncio.wait_for(send(), min(remaining, self.settings.request_timeout))
            except (asyncio.TimeoutError, httpx.TimeoutException):
                return result('uncertain','timeout_after_send') if observation['request_started'] else result('failed','timeout_before_send')
            except httpx.HTTPError:
                return result('uncertain','network_after_send') if observation['request_started'] else result('failed','network_before_send')
            finally:
                token = ''
                known_tokens.clear()
            observation['phase']='response_classification'
            status=observation['http_status']
            error = payload.get('error') if isinstance(payload,dict) else None
            if isinstance(error,dict):
                code = error.get('code') if type(error.get('code')) is int else None
                subcode = error.get('error_subcode') if type(error.get('error_subcode')) is int else None
                # Only bounded numeric diagnostics, never Meta message/type/payload.
                code = code if code is not None and 0 <= code <= 2147483647 else None
                subcode = subcode if subcode is not None and 0 <= subcode <= 2147483647 else None
                observation.update(error_code=code,error_subcode=subcode)
            else:
                code=None
            if status==429 or code in {4,17,32,613,80001,80002,80003,80004,80005,80006}:
                self.store.governor.rate_limited()
                seconds=max(instruction or 0,self.settings.cooldown_seconds)
                self.store.cooldown(token_key,seconds,reason='meta_http_429' if status==429 else 'meta_rate_limit',source='http_status' if status==429 else 'error_code',error_code=code)
                return result('failed','rate_limited',math.ceil(seconds))
            if code in {102,190} or status==401:
                return result('failed','token_invalid')
            if code in {10,200} or status==403:
                return result('failed','permission_denied')
            if (200 <= status < 300 and isinstance(payload,dict)
                    and 'error' not in payload and payload.get('success') is True):
                return result('succeeded','success_boolean_true')
            if isinstance(error,dict) and 400 <= status < 500:
                return result('failed','upstream_error')
            if observation['response_issue'] is None:
                observation['response_issue'] = (
                    'http_not_2xx' if not 200 <= status < 300 else 'json_not_object' if not isinstance(payload,dict)
                    else 'error_field_present' if 'error' in payload else 'success_missing' if 'success' not in payload
                    else 'success_not_boolean' if type(payload['success']) is not bool else 'success_not_true')
            return result('uncertain','response_unverified')
        except AppError:
            raise
        except Exception as error:
            observation['internal_code']=delete_internal_code(error,observation['phase'])
            return result('uncertain','internal_error')
        finally:
            token=''

    async def accounts(self, token: str, token_key: str):
        pages = {}
        seen_cursors = set()
        seen_pages = set()
        cursor = None
        completed = 0
        deadline = time.monotonic() + self.settings.operation_timeout
        try:
            for number in range(self.settings.max_pages):
                self.store.check_cooldown(token_key)
                payload = await self._request(token, token_key, cursor, deadline)
                data = payload.get("data")
                if not isinstance(data, list) or len(data) > self.settings.page_size:
                    raise AppError("upstream_error", 502)
                parsed_pages = [self._page(item) for item in data]
                # An empty page with next is legal; repeated nonempty page indicates no progress.
                signature = hashlib.sha256(json.dumps(sorted(page.id for page in parsed_pages)).encode()).digest()
                if parsed_pages and signature in seen_pages:
                    raise AppError("pagination_incomplete", 502)
                if parsed_pages:
                    seen_pages.add(signature)
                for page in parsed_pages:
                    if page.id not in pages:
                        pages[page.id] = page
                completed += 1
                paging = payload.get("paging", {})
                if not isinstance(paging, dict):
                    raise AppError("pagination_incomplete", 502)
                if "next" not in paging:
                    return list(pages.values())
                cursor = self._next_cursor(paging)
                if token in cursor or any(page.access_token and page.access_token in cursor for page in pages.values()):
                    raise AppError("pagination_incomplete", 502)
                if cursor in seen_cursors:
                    raise AppError("pagination_incomplete", 502)
                seen_cursors.add(cursor)
            raise AppError("pagination_incomplete", 502)
        except asyncio.CancelledError:
            if completed:
                failure = AppError("pagination_incomplete", 504)
                failure.cause_code = "timeout"
                raise failure from None
            raise
        except AppError as error:
            if completed and error.code != "pagination_incomplete":
                # Preserve cause code and cooldown, but never return a partial list.
                failure = AppError("pagination_incomplete", error.status, error.retry_after)
                failure.cause_code = error.code
                raise failure from None
            raise

    def _page(self, item):
        if not isinstance(item, dict):
            raise AppError("upstream_error", 502)
        page_id, name, token = item.get("id"), item.get("name"), item.get("access_token", "")
        if (not isinstance(page_id, str) or not re.fullmatch(r"[0-9]{1,40}", page_id)
                or not isinstance(name, str) or not name or len(name) > 512
                or not isinstance(token, str) or len(token) > self.settings.max_token_length
                or (token and not re.fullmatch(r"[!-~]+", token))):
            raise AppError("upstream_error", 502)
        name = "".join(character for character in name if character.isprintable())
        return Page(page_id, name, token)

    def _next_cursor(self, paging, page_id=None, edge=None):
        link = paging["next"]
        if not isinstance(link, str) or len(link) > 32768:
            raise AppError("pagination_incomplete", 502)
        try:
            parsed = urlsplit(link)
            path_pattern = ("/" + re.escape(self.settings.graph_version) + r"/(me|[0-9]{1,40})/accounts/?" if page_id is None else
                            "/" + re.escape(self.settings.graph_version) + "/" + re.escape(page_id) + "/" + re.escape(edge) + "/?")
            if (parsed.scheme != "https" or parsed.hostname != "graph.facebook.com"
                    or parsed.port not in {None, 443} or parsed.username or parsed.password or parsed.fragment
                    or not re.fullmatch(path_pattern, parsed.path)):
                raise ValueError()
            query = parse_qs(parsed.query, max_num_fields=20)
            cursors = paging.get("cursors", {})
            after = cursors.get("after") if isinstance(cursors, dict) else None
            url_cursors = query.get("after", [])
            if len(url_cursors) != 1:
                raise ValueError()
            if after is None:
                after = url_cursors[0]
            if after != url_cursors[0] or not isinstance(after, str) or not re.fullmatch(r"[A-Za-z0-9_+=/\-]{1,4096}", after):
                raise ValueError()
            return after
        except (ValueError, TypeError):
            raise AppError("pagination_incomplete", 502) from None

    def _usage(self, headers, token_key):
        # Only numeric allowlisted values are inspected; raw headers are never retained/logged.
        previous_low=self.store.governor.low_observations
        self.store.governor.usage_unavailable()
        observations=[]
        for header in ("x-app-usage", "x-business-use-case-usage"):
            raw = headers.get(header)
            if not raw or len(raw) > 16384:
                continue
            try:
                usage = json.loads(raw)
            except (ValueError, TypeError, RecursionError):
                continue
            if not isinstance(usage, dict):
                continue
            if header == "x-app-usage":
                items = [('app',usage)]
            else:
                items = [(identity+':'+entry.get('type',''),entry) for identity,entries in usage.items()
                    if isinstance(identity,str) and re.fullmatch('[0-9]{1,40}',identity) and isinstance(entries,list)
                    for entry in entries if isinstance(entry,dict) and isinstance(entry.get('type'),str)
                    and re.fullmatch('[a-z][a-z0-9_]{0,79}',entry['type'])]
            for identity,item in items:
                if not isinstance(item, dict):
                    continue
                if any(key in item and not (type(item[key]) in {int,float} and math.isfinite(item[key]) and 0<=item[key]<=10000)
                       for key in ('call_count','total_time','total_cputime')):
                    continue
                metrics={key:item[key] for key in ('call_count','total_time','total_cputime')
                    if type(item.get(key)) in {int,float} and math.isfinite(item[key]) and 0<=item[key]<=10000}
                if metrics:
                    observations.append((header,identity,metrics,max(metrics.values())))
        # Duplicate rows for one bucket are one observation: retain the highest
        # reported metric so a low row cannot rearm a still-saturated bucket.
        grouped={}
        for source,identity,metrics,percent in observations:
            key=(source,identity)
            combined=grouped.setdefault(key,{})
            for metric,value in metrics.items(): combined[metric]=max(combined.get(metric,0),value)
        observations=[(source,identity,metrics,max(metrics.values()))
                      for (source,identity),metrics in grouped.items()]
        # Record all scopes first; a low BUC row must not speed up pacing while
        # another known bucket is high. Exactly one recovery vote per response.
        governor=self.store.governor
        for source,identity,metrics,percent in sorted(observations,key=lambda item:item[3]):
            bucket=source+':'+token_key+':'+self.store.token_key(identity)
            governor.observe_usage(percent,bucket=bucket,source=source,metrics=metrics,adapt=False,complete=len(metrics)==3)
            if percent>=100 and bucket in governor.usage_states and bucket not in governor.saturation_paused:
                # Local saturation safety, not an inferred app-wide Meta order.
                self.store.cooldown(token_key,self.settings.cooldown_seconds,reason='local_usage_saturated',source=source,usage=percent)
                if bucket in governor.usage_states: governor.saturation_paused.add(bucket)
            if percent>=100: governor.interval=self.settings.graph_max_interval
        if observations:
            source,identity,metrics,percent=max(observations,key=lambda item:item[3])
            governor.low_observations=previous_low
            governor.observe_usage(percent,bucket=source+':'+token_key+':'+self.store.token_key(identity),source=source,metrics=metrics,complete=len(metrics)==3)

    async def _request(self, token, token_key, cursor, deadline):
        params = {"fields": "id,name,access_token", "limit": str(self.settings.page_size)}
        if cursor is not None:
            params["after"] = cursor
        endpoint = "https://graph.facebook.com/" + self.settings.graph_version + "/me/accounts"
        return await self._get(token, token_key, endpoint, params, deadline)

    async def content_page(self, page_id, edge, fields, token, token_key, limit, cursor, deadline, extra=None, on_request=None):
        if edge not in {"published_posts", "video_reels", "stories"} or not re.fullmatch(r"[0-9]{1,40}", page_id):
            raise AppError("invalid_page")
        endpoint = "https://graph.facebook.com/" + self.settings.graph_version + "/" + page_id + "/" + edge
        params = {"fields": fields, "limit": str(limit), **(extra or {})}
        if cursor is not None:
            params["after"] = cursor
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AppError("timeout", 504)
        try:
            return await asyncio.wait_for(self._get(token, token_key, endpoint, params, deadline, on_request), remaining)
        except asyncio.TimeoutError:
            # Windows monotonic clock resolution can make asyncio timers wake
            # before this absolute deadline. Do not misclassify session expiry
            # or save a result in that gap; the cancelled page is not retried.
            while time.monotonic() < deadline:
                await asyncio.sleep(deadline-time.monotonic())
            raise AppError("timeout", 504) from None

    async def _get(self, token, token_key, endpoint, params, deadline, on_request=None, observation=None):
        known_before=self.store.known_tokens() | {token} if observation is not None else set()
        for attempt in range(3):  # One initial attempt, at most two retries.
            self.store.check_cooldown(token_key)
            if time.monotonic() >= deadline:
                raise AppError("timeout", 504)
            transient, instruction = False, None
            try:
                async with self._stream(token,token_key,endpoint,params,deadline,on_request) as response:
                    status, headers = response.status_code, response.headers
                    if 300 <= status < 400:
                        raise AppError("upstream_error", 502)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > self.settings.max_response_bytes:
                            raise AppError("upstream_error", 502)
                        body.extend(chunk)
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeError, RecursionError):
                        payload = None
                instruction = retry_after(headers.get("retry-after"))
                error = payload.get("error") if isinstance(payload, dict) else None
                if observation is not None:
                    observation.clear()
                    observation['http_status']=status
                    if isinstance(error,dict):
                        tokens=known_before | self.store.known_tokens() | {token}
                        for key in ('code','error_subcode'):
                            value=error.get(key)
                            observation['error_code' if key=='code' else 'error_subcode']=value if type(value) is int and 0<=value<=2147483647 else None
                        for key in ('message','error_user_msg'):
                            observation['error_message' if key=='message' else key]=sanitized_meta_message(error.get(key),tokens)
                code = error.get("code") if isinstance(error, dict) else None
                code = code if type(code) is int else None
                if code in {102, 190} or status == 401:
                    raise AppError("token_invalid", 401)
                if code in {10, 200}:
                    raise AppError("permission_denied", 403)
                if status == 429 or code in {4, 17, 32, 613, 80001, 80002, 80003, 80004, 80005, 80006}:
                    self.store.governor.rate_limited()
                    seconds = max(instruction or 0, self.settings.cooldown_seconds)
                    # App/unknown/page scope conservatively stops all local Graph work.
                    self.store.cooldown(token_key,seconds,reason='meta_http_429' if status==429 else 'meta_rate_limit',source='http_status' if status==429 else 'error_code',error_code=code)
                    raise AppError("rate_limited", 429, math.ceil(seconds))
                if status == 403:
                    raise AppError("permission_denied", 403)
                if 200 <= status < 300 and error is None:
                    if not isinstance(payload, dict):
                        raise AppError("upstream_error", 502)
                    return payload
                transient = (status in {500, 502, 503, 504} or
                             (isinstance(error, dict) and error.get("is_transient") is True))
                failure = AppError("upstream_error", 502)
            except httpx.TimeoutException:
                transient, failure = True, AppError("timeout", 504)
            except (httpx.NetworkError, httpx.RemoteProtocolError):
                transient, failure = True, AppError("network_error", 502)
            except httpx.HTTPError:
                raise AppError("network_error", 502) from None
            if not transient:
                raise failure from None
            wait = self.settings.retry_base * (2 ** attempt) + random.uniform(0, self.settings.retry_jitter)
            if instruction is not None:
                wait = max(wait, instruction)
                self.store.cooldown(token_key,instruction,reason='retry_after',source='retry-after')
                if instruction > self.settings.max_retry_wait or time.monotonic() + wait >= deadline:
                    raise AppError("cooldown", 429, max(1, math.ceil(instruction)))
            if attempt == 2:
                raise failure from None
            if wait > self.settings.max_retry_wait or time.monotonic() + wait >= deadline:
                raise AppError("timeout", 504)
            retry_at = time.monotonic()+wait
            # Windows asyncio timers may wake early; honor the actual deadline.
            while time.monotonic() < retry_at:
                await asyncio.sleep(retry_at-time.monotonic())
        raise AppError("upstream_error", 502)

    @asynccontextmanager
    async def _stream(self,token,token_key,endpoint,params,deadline,on_request):
        async with self.store.governor.slot(token_key,deadline):
            await self.store.governor.pace_dispatch(token_key,deadline,on_request)
            async with self.http.stream('GET',endpoint,params=params,headers={'Authorization':'Bearer '+token}) as response:
                self._usage(response.headers,token_key)
                instruction=retry_after(response.headers.get('retry-after'))
                if instruction is not None:
                    self.store.cooldown(token_key,instruction,reason='retry_after',source='retry-after')
                if response.status_code==429:
                    self.store.cooldown(token_key,max(instruction or 0,self.settings.cooldown_seconds),reason='meta_http_429',source='http_status')
                yield response
