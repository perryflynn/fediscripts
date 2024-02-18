# fediscripts

Collection of Mastodon maintenance scripts.

## spamdetect

This script will search for SPAM messages by blurhashes of images in statuses
and by simple text contains checks.

Requires an admin API token to work.

If no `MASTODON_MIN_ID` provided, the last 24 hours will be processed.
See `start_date` in code for details.

If dry run mode is disabled, all matching accounts will be suspended and
the data will be deleted.

Example:

```sh
MASTODON_MIN_ID=111952803507394588 MASTODON_DRY_RUN=1 \
MASTODON_TOKEN="XXXXXXXXXXXXXXXX" \
    ./spamdetect.py
```

The script will save the last status in a text file named `spamlaststatus`.
The following script allows it to launch the script automatically with the
last status id processed on the previous run:

```sh
if [ ! -f spamlaststatus ]; then echo -n > spamlaststatus; fi && \
( \
    MASTODON_MIN_ID=$(cat spamlaststatus) MASTODON_DRY_RUN=0 \
    MASTODON_TOKEN="XXXXXXXXXXXXXXXXXX" \
    ./spamdetect.py \
)
```
