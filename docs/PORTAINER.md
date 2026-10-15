# Portainer deployment

This fork adds daily automatic account-wide ad extension to the upstream CLI. Source is
checked out directly at `/home/code/projects/docker`, the existing workspace root.
`origin` is `Tomblarom/kleinanzeigen-bot`; `upstream` is
`Second-Hand-Friends/kleinanzeigen-bot`. Deployment files live on
`chore/portainer-deployment`, keeping upstream `main` available for feature branches.
The fork uses `chore/portainer-deployment` as its default branch so its deployment
workflow can be dispatched without adding deployment commits to `main`.

The Portainer stack is `kleinanzeigen-bot` on `ari-dock` (environment 3). It uses
a tested image from this fork, pinned by digest, and a named `bot-data` volume.
The GHCR image is public, matching the public fork. Only application source and
dependencies are included; credentials, ads and browser profiles stay in the volume.
Use the container console in Portainer (`/bin/bash`) and its logs for operation.
An on-demand noVNC desktop on port 6080 supports manual login and SMS verification.

## Initial setup

1. In Portainer's stack environment variables, set `KLEINANZEIGEN_BOT_USERNAME`
   and `KLEINANZEIGEN_BOT_PASSWORD`, then update the stack. Alternatively edit
   `/mnt/data/config.yaml` using `nano` in the container console; this file is
   stored only in the data volume. Never put credentials into Git or chat.
2. In the container console run `bot extend --online --ads=all --dry-run`.
   Complete any account verification requested by Kleinanzeigen. This reads all
   account ads and previews active ads expiring between today and eight days from
   today (inclusive), using Europe/Berlin dates. It does not change ads.
3. After reviewing the preview, run `touch /mnt/data/automation.enabled`.
   Every Berlin calendar day, the scheduler draws one random start between 15:00
   and 18:00 and runs `bot extend --online --ads=all`. All eligible account ads
   are processed sequentially in that batch, including ads without local files
   and ads disabled locally. Reserved, inactive and expired ads are skipped.

Extensions retain the existing listing and its watchers. The scheduler does not
publish, delete, download, change prices or purchase promotion options. Its scope
is the whole extension window, rather than only the last validity day. Local
`republication_interval` settings have no effect on this scheduler.

The chosen time, completion status and hourly retries are saved atomically in
`/mnt/data/.automation-schedule.json`. Container restarts keep the daily plan.
Interrupted or failed runs retry with a fresh account list; successful extensions
are skipped on retry because their expiry moved beyond the extension window.
Failures retry hourly through the end of the Berlin calendar day. At midnight a
new afternoon plan replaces the old one; missed days are not caught up in the
morning. A first activation after 18:00 plans the following afternoon. An existing
interrupted or retrying plan resumes on the next eligible scheduler tick.
Each run is limited to 30 minutes, including CAPTCHA waits; its process group is
stopped on timeout or container shutdown. A busy browser lock is retried too.

`bot extend --ads=all` retains its existing behavior based on local active ad
files and updates their `updated_on` metadata. Online mode neither loads nor
writes local ad files; it confirms success from the refreshed account expiry.
Missing or invalid expiry dates are logged and skipped. Incomplete account lists
abort before any extension. Partial or unconfirmed extensions return a failure
status for scheduler retries. `--ads=123,456` restricts either mode to those IDs.

Headless operation can require manual intervention for CAPTCHA or login problems.
The configured CAPTCHA handling waits and retries; it does not solve challenges.
See upstream [browser troubleshooting](BROWSER_TROUBLESHOOTING.md) for headed
browser setup when manual interaction is required. Account login and live
republishing cannot be verified until your account and ads are configured.

## Manual login with a visible browser

The image includes Chromium, a virtual display and noVNC. The desktop only runs
while `browser-login` is active. Compose binds port 6080 to loopback by default;
for access from your LAN, set the stack variable `BROWSER_UI_BIND` to the Docker
host's LAN address (on ari-dock: `10.0.0.33`). Keep this port on your trusted LAN:
the HTTP desktop connection is unencrypted. Do not forward it to the internet.

1. In the container console run:

   ```bash
   rm -f /mnt/data/automation.enabled
   browser-login
   ```

   On first use, choose and confirm a separate VNC password. VNC uses only eight
   characters; do not reuse your Kleinanzeigen password. The VNC password file
   persists in `/mnt/data/.vnc/passwd`. Keep the console open during login.
2. Open `http://10.0.0.33:6080/vnc.html` on your computer, connect, and enter the
   VNC password. In the displayed Chromium window, visit Kleinanzeigen and log in
   manually. Complete SMS verification there; do not send the code in chat.
3. Close Chromium using its window close button. This stops the desktop and
   releases the shared browser-profile lock. Closing the noVNC tab alone does not
   stop Chromium. `Ctrl+C` in the container console also ends the desktop session.
4. In the container console run `bot extend --online --ads=all --dry-run`. The
   bot reuses the configured browser profile. Its automated runs remain headless.
   After reviewing the preview, enable automation again with
   `touch /mnt/data/automation.enabled`.

The helper uses `browser.user_data_dir` and `browser.profile_name` from the config;
an explicit `--user-data-dir=...` browser argument takes precedence. Keep
`browser.use_private_window: false` so bot runs reuse the saved login. A manual
login does not guarantee that the site accepts subsequent automated requests.
To change the VNC password while the desktop is stopped, run
`x11vnc -storepasswd /mnt/data/.vnc/passwd`.

## Operation and changes

- Pause future scheduled runs: `rm -f /mnt/data/automation.enabled`. This does not
  interrupt a run already in progress. Stop the container if an immediate stop is needed.
- Review the current configuration: `nano /mnt/data/config.yaml`.
- `bot` uses a file lock, so manual and scheduled runs cannot overlap.
- Back up the entire named volume, including YAML, images, IDs and browser profile.
- Local setup: `python3 -m venv .venv`, `.venv/bin/pip install pdm`, then
  `.venv/bin/pdm install`. Follow `CONTRIBUTING.md` for upstream changes.
- For a future upstream PR, branch from `upstream/main` so deployment-specific
  commits are not included.
- Build and check on this host: `docker build --network=host -t kleinanzeigen-bot:dev .`, then
  `bash scripts/check_image.sh kleinanzeigen-bot:dev`.
- Publish: `ari-deploy publish --ref chore/portainer-deployment --wait` after
  committing and pushing changes to the deployment branch.
- Deploy: `ari-deploy up --stack kleinanzeigen-bot --compose compose.yaml
  --image ghcr.io/tomblarom/kleinanzeigen-bot@sha256:DIGEST --apply`.
  If credentials are managed through this helper, use a mode-600 env file under
  `~/.config/ari-code/secrets/` with `--env-file`. Include it in every redeployment
  so previously configured stack credentials are preserved.

All image checks and repository tests run offline with respect to Kleinanzeigen.
