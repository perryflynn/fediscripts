#!/usr/bin/python3

# This script looks for SPAM toots based on images and toot content
# by Christian <christian@serverless.industries>

import requests
import os
import traceback
import time
import json
import yaml
import sys
from datetime import datetime, timezone, timedelta
from pprint import pprint
from typing import Tuple, List, Generator

# parameters
INSTANCE = os.environ.get('MASTODON_INSTANCE', 'einbeck.social')
TOKEN = os.environ.get('MASTODON_TOKEN')
MIN_ID = os.environ.get('MASTODON_MIN_ID', None)
TIME_DELTA = os.environ.get('MASTODON_START_SECONDS', str(60 * 60 * 24))
DRY_RUN = os.environ.get('MASTODON_DRY_RUN', '1') != '0'
DEBUG_STATUS = os.environ.get('MASTODON_DEBUG_STATUS', None)
SPAMLIST = os.environ.get('MASTODON_SPAMLIST', 'https://github.com/perryflynn/fediscripts/raw/main/spamlist.yml')

if not TIME_DELTA.isdigit():
    print(f"MASTODON_START_SECONDS must be a positive amount of seconds, abort.")
    sys.exit(1)

# start point in public timeline if no min_id is provided
start_date = datetime.utcnow() - timedelta(seconds=int(TIME_DELTA))

# load spam list from url
spamlistr = requests.get(SPAMLIST, allow_redirects=True)

if spamlistr.status_code != 200:
    print(f"Requesting the spamlist from URL returned a HTTP Status {spamlistr.status_code}, abort.")
    sys.exit(1)

spamlistdict = None
try:
    spamlist = spamlistr.content.decode("utf-8")
    spamlistdict = yaml.safe_load(spamlist)
except Exception as e:
    print(f"Unable to parse spamlist yaml")
    traceback.print_exc()
    sys.exit(1)

isspamlist = (
    isinstance(spamlistdict, dict) and 'spamlist' in spamlistdict and
    isinstance(spamlistdict['spamlist'], list) and len(spamlistdict['spamlist']) > 0 and
    isinstance(spamlistdict['spamlist'][0], dict)
)

if not isspamlist:
    print("The spamlist format is invalid.")
    sys.exit(0)

rules = spamlistdict['spamlist']

print(f"Loaded {len(rules)} rules")

# calculate start status id
# https://shkspr.mobi/blog/2022/11/building-an-on-this-day-service-for-mastodon/
start_min_id = ( int( start_date.timestamp() ) << 16 ) * 1000

# some variables used below
authheader = { 'Authorization': f"Bearer {TOKEN}" }
last_status = { 'id': str(start_min_id), 'created_at': datetime.fromtimestamp(0).isoformat() }


def has_min_mentions(status: dict, min_mentions: int) -> bool:
    """ Returns true if a toot has the required amount of mentions """
    if min_mentions <= 0:
        return True

    return (
        'mentions' in status and status['mentions'] and isinstance(status['mentions'], list) and
        len(status['mentions']) >= min_mentions
    )


def has_media_attachments(status: dict) -> bool:
    """ Returns true if a status has attachments """
    return (
        'media_attachments' in status and status['media_attachments'] and
        isinstance(status['media_attachments'], list) and len(status['media_attachments']) > 0
    )


def has_card(status: dict) -> bool:
    """ Returns true if a status has a card """
    return 'card' in status and status['card'] and 'type' in status['card']


def has_image_card(status: dict) -> bool:
    """ Returns true if a status has a image card """
    return (
        has_card(status) and 'image' in status['card'] and 'blurhash' in status['card'] and
        isinstance(status['card']['blurhash'], str) and len(status['card']['blurhash']) > 0
    )


def card_contains(status: dict, text: str) -> bool:
    """ Checks if a status card contains a string """
    return (
        ('title' in status['card'] and isinstance(status['card']['title'], str) and text in status['card']['title']) or
        ('url' in status['card'] and isinstance(status['card']['url'], str) and text in status['card']['url']) or
        ('description' in status['card'] and isinstance(status['card']['description'], str) and text in status['card']['description']) or
        ('provider_name' in status['card'] and isinstance(status['card']['provider_name'], str) and text in status['card']['provider_name'])
    )


def get_media_blurhashes(status: dict) -> Generator[str, None, None]:
    """ Get blurhashes from attachments and cards """
    if has_media_attachments(status):
        for media in status['media_attachments']:
            if 'blurhash' in media and isinstance(media['blurhash'], str) and len(media['blurhash']) > 0:
                yield media['blurhash']

    if has_image_card(status):
        yield status['card']['blurhash']


def filter_by_rules(status: dict, rules: List[dict]) -> Tuple[bool, str]:
    """ Check if status matches certain rules """

    for rule in rules:

        # only apply on toots with at least n mentions
        if 'min_mentions' in rule:
            if not has_min_mentions(status, rule['min_mentions']):
                return (False, 'min_mentions_not_reached')

        # image hashes
        if 'blurhash' in rule:
            hashes = list(get_media_blurhashes(status))
            if rule['blurhash'] in hashes:
                return (True, 'blurhash')

        # content
        if 'content_contains' in rule:
            if rule['content_contains'] in status['content']:
                return (True, 'content_contains')

            if has_card(status) and card_contains(status, rule['content_contains']):
                return (True, 'card_content_contains')

    return (False, 'no_hit')


def handle_spam(hits: dict):
    """ Suspend spam accounts and delete all toots from them """

    # show statuses
    hitaccounts = []
    for hit, reason in hits:
        print(f"type=show_spam_status, id={hit['id']}, created_at={hit['created_at']}, user={hit['account']['acct']}, user_id={hit['account']['id']}, reason={reason}")
        if hit['account']['id'] not in hitaccounts:
            hitaccounts.append(hit['account']['id'])

    # disable accounts and delete the toots
    if not DRY_RUN:
        for hitaccount in hitaccounts:
            print(f"type=ban_account, account={hitaccount}", end='', flush=True)

            actionr = requests.post(f"https://{INSTANCE}/api/v1/admin/accounts/{hitaccount}/action", headers=authheader, params={
                'type': 'suspend'
            })

            print(f", suspend={actionr.status_code}", end='', flush=True)

            actionr = requests.delete(f"https://{INSTANCE}/api/v1/admin/accounts/{hitaccount}", headers=authheader)

            print(f", delete={actionr.status_code}", end='', flush=True)

            print()

        print()


def main():
    """ Interate the timeline from a start id to the end """

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

        statuses = []
        rstatuses = None

        if DEBUG_STATUS:
            rstatuses = requests.get(f"https://{INSTANCE}/api/v1/statuses/{DEBUG_STATUS}", headers=authheader)
            statuses = [ rstatuses.json() ]
        else:
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
            result = filter_by_rules(status, rules)

            if result[0]:
                hits.append((status, result[1]))

            params['min_id'] = status['id']
            last_status = status

        # progress dot
        print('.', end='', flush=True)

        # ensure less than 300 requests in 5 minutes
        sleep = 1.5 - (time.time() - tstart)
        if sleep > 0:
            time.sleep(sleep)

        # abort loop when just one status was loaded
        if DEBUG_STATUS:
            break

    # process hits
    print(f"\n\nDone searching timeline, found {len(hits)} spam statuses")
    handle_spam(hits)


def stream(instance, path='/public', params=None):
    # https://jrashford.com/2023/08/17/how-to-stream-mastodon-posts-using-python/

    url = f'https://{instance}/api/v1/streaming{path}'

    headers = {
        #'connection': 'keep-alive',
        #'content-type': 'application/json',
        #'transfer-encoding': 'chunked',
        'Authorization': f"Bearer {TOKEN}"
    }

    resp = requests.get(url, headers=headers, params=params, stream=True)

    event_type = None
    key_event = 'event: '
    key_data = 'data: '

    for line in resp.iter_lines():
        line = line.decode('UTF-8')

        if key_event in line:
            if event_type is not None:
                raise Exception(f"Event type is already '{event_type}', expected data block")

            line = line.replace(key_event, '')
            event_type = line

        if key_data in line:
            if event_type is None:
                raise Exception("Got data but there is no event type set")

            line = line.replace(key_data, '')
            yield (event_type, json.loads(line))
            event_type = None


def main_streaming():
    """ Start streaming API and wait for new statuses """
    global last_status

    print("Start streaming...")

    for event, status in stream(INSTANCE):
        if event == 'update':
            # progress dot
            print('.', end='', flush=True)

            # check status
            result = filter_by_rules(status, rules)

            # handle spam
            if result[0]:
                print('\n')
                handle_spam([ (status, result[1]) ])

            last_status = status


if __name__ == "__main__":
    try:
        main()
        main_streaming()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
        pprint(last_status)

    print(f"\n\nLast ID: {last_status['id']}")

    with open('spamlaststatus', 'w') as f:
        f.write(last_status['id'])
