#!/usr/bin/python3

# This script looks for SPAM toots based on images and toot content
# by Christian <christian@serverless.industries>

import requests
import os
import traceback
import time
from datetime import datetime, timezone, timedelta
from pprint import pprint

# parameters
INSTANCE = os.environ.get('MASTODON_INSTANCE', 'einbeck.social')
TOKEN = os.environ.get('MASTODON_TOKEN')
MIN_ID = os.environ.get('MASTODON_MIN_ID', None)
DRY_RUN = os.environ.get('MASTODON_DRY_RUN', '1') != '0'

# start point in public timeline if no min_id is provided
start_date = datetime.utcnow() - timedelta(1) #datetime(2024, 2, 18, 0, 0, 0, 0, tzinfo=timezone.utc)

# spam search terms
blurhashes = [
    # discord.gg/ctkpaarr text
    'UTQcblVY%gIU8w8_%Mxu%2Rjayt7.8?bMxRj',
    'UTQcbkVY%gIU8w8_%Mxu%2Rjayt7.8?bMxRj',
    # spam box ctkpaarr
    'UkKBv%k8Oas:t1f9V[ae|;agoJoft7bYovjZ',
    'UkKBv%k8Oas:t1f9V[ae|;afoJofs;bYovjZ',
    # hashes from https://gist.github.com/strafplanet/cfdd10c3a999ac2d14a73cc5d9eae6a9#file-mastospam_sql_1-md
    'UkK2FEk8Oas:t1f9V[ae|;agoJofs;bYowjZ',
    'UkK2FFk8Oas:tKf9V[ae|;agoJoft7bYovjZ',
]

rules = [
    { 'content_contains': '画像が貼れなかったのでメンションだけします', 'min_mentions': 4 }
]

# calculate start status id
# https://shkspr.mobi/blog/2022/11/building-an-on-this-day-service-for-mastodon/
start_min_id = ( int( start_date.timestamp() ) << 16 ) * 1000

# some variables used below
authheader = { 'Authorization': f"Bearer {TOKEN}" }
last_status = { 'id': str(start_min_id), 'created_at': datetime.fromtimestamp(0).isoformat() }


def filter_by_rules(status, rules):
    """ Check if status matches certain rules """

    for rule in rules:
        if not rule['content_contains']:
            raise Exception('Rule does not contains content_contains!')

        return (
            (rule['min_mentions'] < 0 or ('mentions' in status and status['mentions'] and len(status['mentions']) >= rule['min_mentions'])) and
            ('content' in status and status['content'] and rule['content_contains'] in status['content'])
        )


def filter_by_media(status, hashes):
    """ Check if status contains a media blurhash on the list """

    if 'media_attachments' in status and status['media_attachments']:
        return any(filter(lambda x: x['blurhash'] in hashes, status['media_attachments']))

    return False


def main():
    """ Main function of this script """

    global last_status
    global start_min_id

    if DRY_RUN:
        print("Dry run is enabled.")

    # start parameters
    if MIN_ID:
        start_min_id = MIN_ID
        print(f"First ID: {MIN_ID}")
        last_status['id'] = MIN_ID

    params = { 'min_id': start_min_id, 'limit': 40 }

    # paginate through the public timeline
    page = 0
    hits = []
    while True:
        tstart = time.time()

        # fetch next statuses
        rstatuses = requests.get(f"https://{INSTANCE}/api/v1/timelines/public", headers=authheader, params=params)
        statuses = rstatuses.json()

        # check for rate limit
        if isinstance(statuses, dict) and 'error' in statuses:
            print(f"Response is not a list of statuses:")
            pprint(statuses)
            print(f"X-Ratelimit-Remaining: {rstatuses.headers['X-Ratelimit-Remaining']}")
            print(f"X-Ratelimit-Reset: {rstatuses.headers['X-Ratelimit-Reset']}")
            break

        # quit on empty status list
        if (len(statuses) < 1):
            break

        # check statuses
        page += 1
        for status in sorted(statuses, key=lambda x: x['id']):
            if filter_by_media(status, blurhashes) or filter_by_rules(status, rules):
                hits.append(status)

            params['min_id'] = status['id']
            last_status = status

        # progress dot
        print('.', end='', flush=True)

        # ensure less than 300 requests in 5 minutes
        sleep = 1.5 - (time.time() - tstart)
        if sleep > 0:
            time.sleep(sleep)

    # process hits
    print(f"\n\nDone searching timeline, found {len(hits)} spam statuses")

    # show statuses
    hitaccounts = []
    for hit in hits:
        print(f"id={hit['id']}, created_at={hit['created_at']}, user={hit['account']['acct']}, user_id={hit['account']['id']}")
        if hit['account']['id'] not in hitaccounts:
            hitaccounts.append(hit['account']['id'])

    # disable accounts
    if not DRY_RUN:
        for hitaccount in hitaccounts:
            actionr = requests.post(f"https://{INSTANCE}/api/v1/admin/accounts/{hitaccount}/action", headers=authheader, params={
                'type': 'suspend'
            })

            print(f"POST {actionr.status_code} => '{actionr.text}'")

            actionr = requests.delete(f"https://{INSTANCE}/api/v1/admin/accounts/{hitaccount}", headers=authheader, params={
                'type': 'suspend'
            })

            print(f"DELETE {actionr.status_code} => '{actionr.text}'")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
        pprint(last_status)

    print(f"\n\nLast ID: {last_status['id']}")

    with open('spamlaststatus', 'w') as f:
        f.write(last_status['id'])
