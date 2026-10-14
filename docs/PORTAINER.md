# Portainer deployment

This fork adds an unattended Docker deployment to the upstream CLI. Source is
checked out directly at `/home/code/projects/docker`, the existing workspace root.
`origin` is `Tomblarom/kleinanzeigen-bot`; `upstream` is
`Second-Hand-Friends/kleinanzeigen-bot`. Deployment files live on
`chore/portainer-deployment`, keeping upstream `main` available for feature branches.
The fork uses `chore/portainer-deployment` as its default branch so its deployment
workflow can be dispatched without adding deployment commits to `main`.

The Portainer stack is `kleinanzeigen-bot` on `ari-dock` (environment 3). It uses
a tested image from this fork, pinned by digest, and a named `bot-data` volume.
It exposes no web interface or network port. Use the container console in Portainer
(`/bin/bash`) and its logs for setup and operation.

## Initial setup

1. In Portainer's stack environment variables, set `KLEINANZEIGEN_BOT_USERNAME`
   and `KLEINANZEIGEN_BOT_PASSWORD`, then update the stack. Alternatively edit
   `/mnt/data/config.yaml` using `nano` in the container console; this file is
   stored only in the data volume. Never put credentials into Git or chat.
2. In the container console run `bot download --ads=all`. Complete any account
   verification requested by Kleinanzeigen. Ads and images are saved beneath
   `/mnt/data/downloaded-ads`. Review them and set `active: false` on ads that
   must not be published. The browser session persists in the same volume.
3. Edit `/mnt/data/config.yaml`: choose `ad_defaults.republication_interval`
   (default 30 days). Check per-ad intervals in downloaded YAML files, as these
   override the default. Run `bot verify` and `bot status` to inspect the plan.
4. After checking the plan, run `touch /mnt/data/automation.enabled`. The scheduler
   starts within 60 seconds and checks again 24 hours after each completed run.
   It runs `bot verify`, then `bot publish --ads=due` only if validation succeeds.

Republishing creates a new listing and removes the old one after success. Watchers
and saved listings do not carry over, and publication quotas can apply. If you
only want to extend expiry while keeping the listing, use `bot extend --ads=all`
instead; this upstream command only applies within eight days of expiry.

Headless operation can require manual intervention for CAPTCHA or login problems.
The configured CAPTCHA handling waits and retries; it does not solve challenges.
See upstream [browser troubleshooting](BROWSER_TROUBLESHOOTING.md) for headed
browser setup when manual interaction is required. Account login and live
republishing cannot be verified until your account and ads are configured.

## Operation and changes

- Pause future scheduled runs: `rm -f /mnt/data/automation.enabled`. This does not
  interrupt a run already in progress. Stop the container if an immediate stop is needed.
- Review the current configuration: `nano /mnt/data/config.yaml`.
- `bot` uses a file lock, so manual and scheduled runs cannot overlap.
- Back up the entire named volume, including YAML, images, IDs and browser profile.
- Local setup: `python3 -m venv .venv`, `.venv/bin/pip install pdm`, then
  `.venv/bin/pdm install`. Follow `CONTRIBUTING.md` for upstream changes.
- For a future upstream PR, branch from `upstream/main` so deployment-specific
  commits are not included. No upstream PR is opened as part of this setup.
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
