"""Optional Lithic sandbox Events API registration. Never prints the HMAC secret."""
import argparse
from urllib.parse import urlsplit
from lithic_client import lithic_request

p=argparse.ArgumentParser(description='Occlet signed card_transaction.updated subscription (SANDBOX)')
p.add_argument('https_base',help='Temporary public HTTPS tunnel base URL, e.g. https://abc.trycloudflare.com')
a=p.parse_args()
u=urlsplit(a.https_base)
if u.scheme!='https' or not u.hostname or u.username or u.password or u.port not in (None,443):
    p.error('Public HTTPS URL required')
endpoint=a.https_base.rstrip('/')+'/lithic/events'
r=lithic_request('POST','/event_subscriptions',payload={
    'url':endpoint,'description':'Occlet W4 local ledger sandbox reconciliation',
    'event_types':['card_transaction.updated']})
token=r.get('token')
if not token:
    p.error('Provider did not return a subscription token')
print('Events API subscription created: '+str(token))
print('Endpoint: '+endpoint)
print('Retrieve the secret privately with: curl -sS https://sandbox.lithic.com/v1/event_subscriptions/'+str(token)+'/secret -H "Authorization: $LITHIC_API_KEY"')
print('Set LITHIC_EVENT_SECRET in the local app environment, then restart; never paste secrets in chat.')
