# University mail (read-only Gmail)

**Status: implemented and tested against fakes; never connected to a real account. It does nothing until you
complete the steps below.** PIA has not accessed any mailbox.

## What it does, and what it cannot do

Once connected, PIA periodically asks Gmail for recent messages that match a search (senders you list, or
announcement-like keywords), and stores each as an announcement in the University space, where it is classified,
ranked and, if it is an opportunity, extracted like any other item.

- It asks Google for **one permission**: `gmail.readonly`. With that permission Google itself rejects any attempt
  to send, change, label or delete mail. Read-only does not depend on PIA's code being correct.
- It does not read the whole mailbox: only messages matching the search, newest first, 40 at most per check.
- Message text is truncated to 6,000 characters before storage. Attachments are never downloaded.
- The authorisation is stored in `data/secrets/gmail.json` on the machine running PIA. It is never put in the
  database, the logs or the web API.
- If you use a model key, the text of matching messages is sent to that model for summarising, like any other
  item. If you would rather mail never left your machine, run with `PIA_NO_MODELS=1` or do not connect mail.

## Set up (about ten minutes, once)

You create your own Google OAuth client, so no third party sits between you and your mailbox.

1. Go to console.cloud.google.com and create a project (any name).
2. APIs and Services, Library: enable **Gmail API**.
3. APIs and Services, OAuth consent screen: choose **External**, fill in the app name and your email, add the
   scope `.../auth/gmail.readonly`, and add your university address under **Test users**.
4. APIs and Services, Credentials, Create credentials, **OAuth client ID**, application type **Desktop app**.
5. Put the two values in `.env`:

   ```
   GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=...
   ```

6. Run:

   ```bash
   pia gmail-connect --senders "notices@your-institute.example,dean@your-institute.example"
   ```

   Your browser opens Google's consent page. Check that the only permission listed is reading mail, and approve.
   The source appears under Sources as "University mail".

Things to know:

- **Your university may block third-party apps.** If Google says the app is blocked by your administrator, the
  integration cannot be used with that account; the alternative is to forward or filter announcements to a
  personal account and connect that instead.
- While the consent screen is in **Testing** mode, Google expires the authorisation after 7 days and you must
  run `pia gmail-connect` again. Publishing the app (still for your own use) removes that limit, but Google may
  ask to verify an app that requests a restricted scope. PIA reports "authorisation expired" clearly when this
  happens; nothing else is affected.
- For a cloud deployment, run `pia gmail-connect` on a machine with a browser and copy `data/secrets/gmail.json`
  into the server's volume.

## Disconnect

```bash
pia gmail-disconnect
```

This deletes the stored authorisation, asks Google to revoke it and pauses the source. Items already acquired
from mail stay. You can also remove PIA's access at myaccount.google.com, Security, Third-party access.

## Design notes

- OAuth 2.0 for installed applications with PKCE and a loopback redirect on a random local port.
- Plain HTTP calls over PIA's shared retry layer; no Google SDK.
- Tests (`tests/test_platform_misc.py`) check the scope constant, that the module contains no modifying
  endpoint, that only GET requests reach the Gmail API, token refresh, and the "not connected" and "revoked"
  paths.
