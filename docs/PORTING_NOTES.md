# Porting Notes

Guidance for anyone extracting logic from the legacy `internal-automation` repo into this
codebase. `internal-automation` stays untouched and reference-only — never modify it, and never
copy any of the following verbatim into this repo, even as "realistic-looking" test fixtures:

1. `internal-automation/data/config.json` contains real Azure AD tenant/client IDs, a real
   Fishbowl ERP hostname, and real vendor/customer contact info. If a test fixture needs a
   config shape, hand-write synthetic data instead.
2. `internal-automation/src/facade/ftp/ftp_facade.py` hardcodes a real FTP host IP and username.
   If SFTP ingestion is ever built here, do not reference or reuse these values even in a
   docstring or code comment.
3. `internal-automation/src/util/inventory/master_inventory_util.py` hardcodes a Windows path
   under one person's local user directory. If porting pricing logic from that file, take the
   formula, not the surrounding I/O code.
4. `internal-automation/src/util/constants/credentials.py` contains, in cleartext, the same FTP
   credentials as item 2 plus a real Fishbowl host/username and real Azure AD tenant/client IDs
   with Outlook folder GUIDs. Same rule: never copy, even as an example.
