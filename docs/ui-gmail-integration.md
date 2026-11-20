# Compact UI on the Gmail bot

The compact UI at the root `/` runs on the same Deck process, database and VM as the Gmail poller. It adds no new store and no new API.

## Route and authentication

- URL: the Deck host on the owner's public server. The old `/experience.html` address redirects to `/` and keeps the query. See D94.
- Caddy on the owner's public server imports `sso`. Authelia checks each request. The rule is `one_factor` for the `deck` host.
- Caddy forwards to the poller service over the tailnet. The service listens on the tailnet address of this machine. It no longer listens on loopback.
- Deck trusts `Remote-User` from three peers only: loopback, its own tailnet address (for the service's own calls) and the Caddy server. Any other peer gets 401.
- `service.json` keys `bind` and `proxy_peers` set this. Without them the service behaves as before.
- The public sample preview is separate and static. It holds no mail.

## What the UI shows

- One bot row named "Gmail bot", with the account health line.
- The Activity tab shows account health, label state, the last and next summary, and tasks.
- Chat shows the morning summary by its existing chat id.
- `GmailHealth` marks a failed account, a stale account, and a failed refresh as three different states. It never shows a sample value.
- The composer for the Gmail bot is off. A chat message would queue a task that could act on the mailbox. The home destination list leaves the bot out.
- Status polls every five seconds. A failed poll keeps the last status and marks it stale.

## Operate

- Build: `npm --prefix web run build`, then `cargo build --release`.
- Update the binary: stop `deck-gmail-poller`, copy the binary, set `deck_sha256` in `service.json`, start the unit. Keep the VM disk and the database.
- Rollback: `~/.local/share/deck-gmail/rollback/` holds the previous binary, `service.json`, `gmail_service.py` and `deck.toml`.
- Revoke the route: delete the Deck host block in the Caddyfile and reload Caddy.

## Not covered

Owner login through Authelia was not run in a browser. The service restart raises the executor epoch by one. The prototype page `/prototype.html` is still in the binary (no longer the root or the fallback). It is behind the same login and shows sample data.
