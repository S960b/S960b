"""Owner-authenticated UI adapter to the EXISTING fixed MT Unix gateway.

No Git/PAT/SQLite queue, actor selector, worker execution or automatic retry.
An owner message is explicitly a fixed MT report to ChatGPT, not a third actor.
"""
import hashlib
from http import HTTPStatus
import json
import re
import secrets
import threading
import time
from urllib.parse import parse_qsl

from . import DialogError
from .github import sha
from .panel import render_view, security_headers
from .unix_authority import SOCKETS, UnixClient


class GatewayPanel:
    def __init__(self, authority, port, *, client=None):
        self.authority = authority
        self.host = '127.0.0.1:' + str(port)
        self.origin = 'http://' + self.host
        self.client = UnixClient(SOCKETS['mt']) if client is None else client
        self.lock, self.forms, self.uncertain = threading.Lock(), {}, False

    def _form(self, head):
        with self.lock:
            now = time.monotonic()
            self.forms = {k:v for k,v in self.forms.items() if v['state'] != 'new' or now < v['expires']}
            if self.uncertain or len(self.forms) >= 64:
                return '<p>Отправка заблокирована: проверьте историю и сохранённый ID результата. Автоповтора нет.</p>'
            token = secrets.token_urlsafe(32)
            self.forms[token] = {'head':head, 'expires':now+600, 'state':'new'}
        return ('<form method="post" action="/send"><input type="hidden" name="csrf" value="'+token+'">'
            '<label>Сообщение владельца от стороны MT → ChatGPT (только текст, не команда)'
            '<textarea name="body" required maxlength="2048"></textarea></label>'
            '<button type="submit">Отправить через общий gateway</button></form>')

    def _send(self, token, body):
        if not isinstance(body,str) or not body.strip() or len(body.encode('utf-8')) > 2048:
            raise DialogError('panel_body_rejected')
        with self.lock:
            slot = self.forms.get(token)
            if slot is None or slot['state'] != 'new' or time.monotonic() >= slot['expires'] or self.uncertain:
                raise DialogError('panel_form_consumed_or_rejected')
            # Same head+exact body gives the same ID across panel restarts. No
            # refreshed timestamp/random ID can authorize resending this intent.
            mid = hashlib.sha256(('panel-mt-report\0'+slot['head']+'\0'+body).encode()).hexdigest()
            request = {'op':'append','head':slot['head'],'message_id':mid,'kind':'report','body':body}
            slot.update(state='attempted', request=request)
        try:
            result = self.client.call(request)
            if (set(result) != {'message_id','commit','created'} or result['message_id'] != mid or
                    type(result['created']) is not bool):
                raise DialogError('panel_result_unconfirmed')
            sha(result['commit'])
        except Exception:
            with self.lock: self.uncertain = True
            return 503, {'status':'unconfirmed','message_id':mid,'retry_authorized':False,
                         'instruction':'Проверьте историю по этому ID. Не отправляйте заново.'}
        with self.lock: slot.update(state='confirmed', result=result)
        return 200, {'status':'confirmed', **result, 'retry_authorized':False}

    def __call__(self, env, start):
        headers = security_headers()
        headers['Content-Security-Policy'] = headers['Content-Security-Policy'].replace("form-action 'none'", "form-action 'self'")
        code, body = 403, b'Forbidden'
        try:
            if (env.get('HTTP_HOST') != self.host or env.get('REMOTE_ADDR') != '127.0.0.1' or
                    any(k.startswith('HTTP_X_FORWARDED_') or k=='HTTP_FORWARDED' for k in env) or
                    env.get('HTTP_ORIGIN') not in (None,self.origin)):
                raise DialogError('panel_boundary_denied')
            if not self.authority.verify(env.get('HTTP_AUTHORIZATION')):
                code, body = 401, b'Authentication required'
                headers['WWW-Authenticate'] = 'Basic realm="Git Dialog", charset="UTF-8"'
            else:
                code, body = self._route(env, headers)
        except DialogError as exc:
            code = 429 if exc.code == 'authentication_throttled' else 403
            body = b'Request rejected; no automatic retry'
        except Exception:
            code, body = 503, b'Gateway unavailable; no automatic retry'
        headers['Content-Length'] = str(len(body))
        start(str(code)+' '+HTTPStatus(code).phrase, list(headers.items()))
        return [body]

    def _route(self, env, headers):
        method, path, query = env.get('REQUEST_METHOD'), env.get('PATH_INFO'), env.get('QUERY_STRING','')
        if env.get('HTTP_TRANSFER_ENCODING') or env.get('HTTP_EXPECT'):
            raise DialogError('panel_framing_denied')
        if method == 'POST':
            if (path != '/send' or query or env.get('HTTP_ORIGIN') != self.origin or
                    env.get('CONTENT_TYPE') != 'application/x-www-form-urlencoded' or
                    not re.fullmatch(r'[1-9][0-9]{0,3}', env.get('CONTENT_LENGTH',''))):
                raise DialogError('panel_post_denied')
            length = int(env['CONTENT_LENGTH'])
            if length > 8192: raise DialogError('panel_body_rejected')
            raw = env['wsgi.input'].read(length)
            if len(raw) != length: raise DialogError('panel_body_rejected')
            pairs = parse_qsl(raw.decode('utf-8'), keep_blank_values=True, strict_parsing=True,
                              encoding='utf-8', errors='strict', max_num_fields=2)
            obj = dict(pairs)
            if len(pairs)!=2 or set(obj)!={'csrf','body'}: raise DialogError('panel_form_rejected')
            code, result = self._send(obj['csrf'],obj['body'])
            headers['Content-Type'] = 'application/json; charset=utf-8'
            return code, json.dumps(result,ensure_ascii=False).encode()
        if method != 'GET': return 405, b'GET or explicit POST /send only'
        if env.get('CONTENT_LENGTH','') not in ('','0'): raise DialogError('panel_body_rejected')
        if path not in ('/','/history','/api/status','/bridge/status'): return 404,b'Not found'
        pairs = parse_qsl(query,keep_blank_values=True,strict_parsing=True,max_num_fields=2)
        params = dict(pairs)
        if (len(pairs)!=len(params) or set(params)-{'offset','limit'} or
                any(not re.fullmatch(r'[0-9]{1,6}',v) for v in params.values())):
            raise DialogError('panel_page_rejected')
        offset, limit = int(params.get('offset',0)),int(params.get('limit',20))
        if not 1 <= limit <= 20: raise DialogError('panel_page_rejected')
        view = self.client.call({'op':'view','offset':offset,'limit':limit})
        if path in ('/api/status','/bridge/status'):
            headers['Content-Type']='application/json; charset=utf-8'
            return 200,json.dumps({'task_id':view['task_id'],'head':view['head'],
                'waiting_for':view['waiting_for'],'total_messages':view['total_messages'],
                'read_only':False,'writer_actor':'mt','write_path':'shared_unix_gateway',
                'delete_enabled':False}).encode()
        page = render_view(view,offset=offset,limit=limit).decode()
        page = page.replace('Изолированный просмотр. Запись, закрытие и удаление отключены. Это не рабочая панель моста.',
            'Общий gateway. Отправка владельца фиксирована как MT → ChatGPT. Закрытие и удаление отключены.')
        form = self._form(view['head']) if view['waiting_for']=='mt' else '<p>Сейчас ход ChatGPT. Отправка от MT недоступна.</p>'
        return 200,page.replace('</body>',form+'</body>').encode()
