import os, sys
os.environ['ARCHGUARD_TRUSTED_PROXY_IPS'] = '10.0.0.0/8'
from archguard.dashboard._auth import _real_client_ip
from fastapi import Request
request = Request({'type': 'http', 'client': ('10.1.1.1', 12345), 'headers': [(b'x-forwarded-for', b'192.168.1.100, 203.0.113.5')]})
ip = _real_client_ip(request)
print('Resolved IP:', ip)
if ip == '192.168.1.100':
    print('SUCCESS')
else:
    print('FAILED')
