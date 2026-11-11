"""Check a fresh local deployment without printing credentials."""
import json
import os
import secrets
import urllib.request
import http.cookiejar

base = os.environ.get('SMOKE_BASE_URL', 'http://127.0.0.1:8080')
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={'Content-Type':'application/json'})
    with client.open(req, timeout=30) as response:
        return json.load(response)

request('/healthz')
assert request('/api/auth/status')['need_init'], 'Use a fresh isolated deployment for this test.'
request('/api/auth/register', {'username':'smoke_admin', 'display_name':'Smoke Admin', 'password':secrets.token_urlsafe(24)+'A1'})
assert not request('/api/auth/status')['need_init']
with client.open(base + '/', timeout=10) as response:
    html = response.read().decode()
    assert 'Open Lease Manager' in html and '/assets/' in html
print('PASS: healthy, fresh database initialized, administrator registration, frontend served.')
