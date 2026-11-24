#!/usr/bin/env python3
"""Validate *public* OAuth/OIDC metadata of a future Keycloak issuer.
No token or credential is used. No invocation by default, network only with
explicit --issuer. HTTPS same-origin discovery, strict limits and no redirects.
"""
from __future__ import annotations
import argparse, ipaddress, json, socket, ssl, urllib.error, urllib.parse, urllib.request

class MetadataError(Exception):pass

def req(x,code):
    if not x:raise MetadataError(code)

def parse_issuer(value):
    req(isinstance(value,str) and len(value)<300,'INVALID_ISSUER')
    p=urllib.parse.urlsplit(value)
    req(p.scheme=='https' and p.hostname and not p.username and not p.password and
        not p.query and not p.fragment and (p.port in (None,443)) and
        p.path.startswith('/realms/') and not p.path.endswith('/') and '..' not in p.path,
        'INVALID_ISSUER')
    req(p.hostname.lower() not in ('localhost','127.0.0.1') and '.' in p.hostname,'NOT_PUBLIC_ISSUER')
    return value

def metadata_urls(issuer):
    p=urllib.parse.urlsplit(parse_issuer(issuer))
    origin=p.scheme+'://'+p.netloc
    return origin+p.path+'/.well-known/openid-configuration',origin+p.path+'/protocol/openid-connect/certs'

def validate_oidc(meta,issuer):
    req(isinstance(meta,dict),'INVALID_METADATA')
    req(meta.get('issuer')==issuer,'ISSUER_MISMATCH')
    p=urllib.parse.urlsplit(issuer)
    origin=p.scheme+'://'+p.netloc
    for k in ('authorization_endpoint','token_endpoint','jwks_uri'):
        u=meta.get(k)
        req(isinstance(u,str) and u.startswith(origin+'/') and
            urllib.parse.urlsplit(u).netloc==p.netloc and
            urllib.parse.urlsplit(u).scheme=='https','ENDPOINT_ORIGIN_INVALID_'+k)
    req('S256' in meta.get('code_challenge_methods_supported',[]),'PKCE_S256_NOT_ADVERTISED')
    req('code' in meta.get('response_types_supported',[]),'AUTH_CODE_NOT_ADVERTISED')
    req('authorization_code' in meta.get('grant_types_supported',[]),'AUTH_GRANT_NOT_ADVERTISED')
    return {
        'issuer_exact':True,'https_auth_and_token':True,'pkce_s256':True,
        'cimd_advertised':meta.get('client_id_metadata_document_supported') is True,
        'scope_read_advertised':'managed:read' in meta.get('scopes_supported',[]),
        'scope_write_advertised':'managed:write' in meta.get('scopes_supported',[]),
        'issuer_identification':meta.get('authorization_response_iss_parameter_supported') is True,
    }

def validate_public_dns(host):
    results=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    req(bool(results),'NO_DNS')
    for item in results:
        ip=ipaddress.ip_address(item[4][0])
        req(ip.is_global,'NONPUBLIC_IP_REJECTED')

def fetch_json(url):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*a,**k):raise MetadataError('REDIRECT_DENIED')
    opener=urllib.request.build_opener(NoRedirect())
    req(url.startswith('https://'),'NOT_HTTPS')
    with opener.open(urllib.request.Request(url,headers={'Accept':'application/json'}),timeout=5) as r:
        req(r.status==200,'HTTP_NOT_OK')
        raw=r.read(65537)
        req(len(raw)<=65536,'METADATA_TOO_LARGE')
        return json.loads(raw)

def main():
    a=argparse.ArgumentParser()
    a.add_argument('--issuer',help='Explicit REAL public Keycloak issuer URL. Optional.')
    args=a.parse_args()
    if not args.issuer:
        print('STAGE35B_NO_PUBLIC_ISSUER_SELECTED_NO_NETWORK_REQUEST')
        print('STAGE35B_KEYCLOAK_CIMD_RESOURCE_INDICATORS_REQUIRE_REVIEW')
        return
    try:
        issuer=parse_issuer(args.issuer)
        host=urllib.parse.urlsplit(issuer).hostname
        validate_public_dns(host)
        url,jwks=metadata_urls(issuer)
        m=fetch_json(url)
        v=validate_oidc(m,issuer)
        req(m['jwks_uri']==jwks,'JWKS_URI_UNEXPECTED')
        j=fetch_json(jwks)
        req(isinstance(j,dict) and isinstance(j.get('keys'),list) and
            1<=len(j['keys'])<=10,'JWKS_INVALID')
        req(any(isinstance(k,dict) and k.get('kty')=='RSA' and k.get('kid') and
                (k.get('alg') in ('RS256',None)) and 'd' not in k for k in j['keys']),
            'NO_RS256_PUBLIC_KEY')
        print('STAGE35B_PUBLIC_KEYCLOAK_DISCOVERY_VALIDATED')
        for key,val in v.items():print('STAGE35B_CAPABILITY_'+key.upper()+'='+str(val).lower())
        print('STAGE35B_PUBLIC_JWKS_RS256_PRESENT_NO_PRIVATE_KEYS')
    except (MetadataError,ValueError,OSError,urllib.error.URLError,ssl.SSLError,TimeoutError,TypeError,KeyError) as exc:
        err=str(exc) if isinstance(exc,MetadataError) else type(exc).__name__
        print('STAGE35B_STOP_ISSUER_METADATA_'+err[:80])
        raise SystemExit(2)
if __name__=='__main__':main()
