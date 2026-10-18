# Portainer runtime

The root Dockerfile builds the unmodified upstream application and KasmVNC image,
then adds a separate standard-library scheduler. `UPSTREAM_COMMIT` records the
application revision. No Kleinanzeigen Python modules are patched or imported by
the scheduler. `bot login` uses the upstream visible login command.

With `/mnt/data/automation.enabled` present, a persistent daily plan chooses a
start from 15:00 through 18:00 Europe/Berlin. The account batch runs public
`download --ads=all`, then `extend --ads=all` while holding the browser lock.
Upstream filters extensions by active state and the eight-day window. Failed
commands and logged per-ad errors retry hourly through the same calendar day.
There are no publish or delete commands. The named data volume, profile, config,
enable marker and schedule state survive redeployment.

The default deployment is headless. The browser desktop is available on demand
through upstream `bot login`; expose port 6080 only through the established private
access path. Never commit runtime credentials or data.
