"""Offline tests; no filesystem writes outside TemporaryDirectory and no remote calls."""
import ast, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import metadata35b as m
import readiness35b as r

ISS='https://login.example.org/realms/bridge'
DOC={'issuer':ISS,'authorization_endpoint':ISS+'/protocol/openid-connect/auth',
 'token_endpoint':ISS+'/protocol/openid-connect/token',
 'jwks_uri':ISS+'/protocol/openid-connect/certs',
 'code_challenge_methods_supported':['S256'],
 'response_types_supported':['code'],
 'grant_types_supported':['authorization_code'],
 'client_id_metadata_document_supported':True,
 'scopes_supported':['managed:read','managed:write'],
 'authorization_response_iss_parameter_supported':True}
class Tests(unittest.TestCase):
 def test_01_url(self): self.assertEqual(m.parse_issuer(ISS),ISS)
 def test_02_suffix(self): self.assertTrue(m.metadata_urls(ISS)[0].endswith('openid-configuration'))
 def test_03_metadata(self): self.assertTrue(m.validate_oidc(DOC,ISS)['cimd_advertised'])
 def test_04_scopes(self): self.assertTrue(m.validate_oidc(DOC,ISS)['scope_read_advertised'])
 def test_05_pkce(self): self.assertTrue(m.validate_oidc(DOC,ISS)['pkce_s256'])
 def test_06_http(self):
  with self.assertRaises(m.MetadataError):m.parse_issuer('http://login.example.org/realms/bridge')
 def test_07_credential(self):
  with self.assertRaises(m.MetadataError):m.parse_issuer('https://x:y@login.example.org/realms/bridge')
 def test_08_fragment(self):
  with self.assertRaises(m.MetadataError):m.parse_issuer(ISS+'#secret')
 def test_09_localhost(self):
  with self.assertRaises(m.MetadataError):m.parse_issuer('https://localhost/realms/bridge')
 def test_10_port(self):
  with self.assertRaises(m.MetadataError):m.parse_issuer('https://login.example.org:8443/realms/bridge')
 def test_11_wrong_issuer(self):
  with self.assertRaises(m.MetadataError):m.validate_oidc({**DOC,'issuer':ISS+'/'},ISS)
 def test_12_wrong_auth_origin(self):
  with self.assertRaises(m.MetadataError):m.validate_oidc({**DOC,'authorization_endpoint':'https://evil.example.org/auth'},ISS)
 def test_13_wrong_token_origin(self):
  with self.assertRaises(m.MetadataError):m.validate_oidc({**DOC,'token_endpoint':'https://evil.example.org/token'},ISS)
 def test_14_wrong_jwks_origin(self):
  with self.assertRaises(m.MetadataError):m.validate_oidc({**DOC,'jwks_uri':'https://evil.example.org/certs'},ISS)
 def test_15_no_pkce(self):
  with self.assertRaises(m.MetadataError):m.validate_oidc({**DOC,'code_challenge_methods_supported':['plain']},ISS)
 def test_16_no_auth_code(self):
  with self.assertRaises(m.MetadataError):m.validate_oidc({**DOC,'response_types_supported':['token']},ISS)
 def test_17_no_grant(self):
  with self.assertRaises(m.MetadataError):m.validate_oidc({**DOC,'grant_types_supported':['client_credentials']},ISS)
 def test_18_optional_cimd(self):self.assertFalse(m.validate_oidc({**DOC,'client_id_metadata_document_supported':False},ISS)['cimd_advertised'])
 def test_19_optional_scopes(self):self.assertFalse(m.validate_oidc({**DOC,'scopes_supported':[]},ISS)['scope_write_advertised'])
 def test_20_optional_issuer_id(self):self.assertFalse(m.validate_oidc({**DOC,'authorization_response_iss_parameter_supported':False},ISS)['issuer_identification'])
 def test_21_legacy_missing(self):
  with tempfile.TemporaryDirectory() as x:self.assertEqual(r.analyze_source(Path(x)/'gone.py'),{'present':False})
 def test_22_legacy_proof(self):
  with tempfile.TemporaryDirectory() as x:
   p=Path(x)/'legacy.py';p.write_text('class BridgeOAuthProvider:\n pass\ndef require_owner():\n return True\n')
   o=r.analyze_source(p);self.assertTrue(o['has_oauth_provider']);self.assertTrue(o['has_require_owner'])
 def test_23_legacy_not_jwt(self):
  with tempfile.TemporaryDirectory() as x:
   p=Path(x)/'legacy.py';p.write_text('def require_owner():\n pass')
   self.assertFalse(r.analyze_source(p)['has_jwt_verifier_reference'])
 def test_24_legacy_malformed(self):
  with tempfile.TemporaryDirectory() as x:
   p=Path(x)/'legacy.py';p.write_text('def broken()')
   self.assertFalse(r.analyze_source(p)['parseable'])
 def test_25_need(self):
  with self.assertRaises(r.Stop):r.need(False,'TEST')
 def test_26_nonpublic_ip(self):
  with patch('metadata35b.socket.getaddrinfo',return_value=[(0,0,0,'',('REDACTED_IPv4',443))]):
   with self.assertRaises(m.MetadataError):m.validate_public_dns('domain.example')
 def test_27_public_ip(self):
  with patch('metadata35b.socket.getaddrinfo',return_value=[(0,0,0,'',('REDACTED_IPv4',443))]):m.validate_public_dns('domain.example')
 def test_28_missing_metadata(self):
  with self.assertRaises(m.MetadataError):m.validate_oidc({'issuer':ISS},ISS)
 def test_29_wrong_scheme_resource(self):
  with self.assertRaises(m.MetadataError):m.validate_oidc({**DOC,'jwks_uri':'http://login.example.org/realms/bridge/protocol/openid-connect/certs'},ISS)
 def test_30_token_redirect_and_secret(self):
  with self.assertRaises(m.MetadataError):m.parse_issuer(ISS+'?client_secret=oops')
if __name__=='__main__':unittest.main()
