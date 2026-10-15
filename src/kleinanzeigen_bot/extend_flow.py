# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Ad extension browser workflow."""

from __future__ import annotations

from datetime import date, datetime
from gettext import gettext as _
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from . import bulk_ad_actions, published_ads
from .published_ads import ad_matches_id
from .utils import dicts as _dicts

if TYPE_CHECKING:
    from .model.ad_model import Ad
    from .published_ads import PublishedAd
from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils.exceptions import AdBatchError
from .utils.i18n import pluralize
from .utils.web_scraping_mixin import By, WebScrapingMixin

LOG:_loggers.Logger = _loggers.get_logger(__name__)


async def extend_ads(
    web:WebScrapingMixin,
    root_url:str,
    ad_cfgs:list[tuple[str, Ad, dict[str, Any]]],
) -> None:
    """Extends ads that are close to expiry."""
    # Fetch currently published ads from API
    published_ads_list = await published_ads.fetch_published_ads(web, root_url)

    # Filter ads that need extension
    ads_to_extend:list[tuple[str, Ad, dict[str, Any]]] = []
    for ad_file, ad_cfg, ad_cfg_orig in ad_cfgs:
        # Skip unpublished ads (no ID)
        if ad_cfg.id is None:
            LOG.info(" -> SKIPPED: ad '%s' is not published yet", ad_cfg.title)
            continue

        published_ad:PublishedAd | None = next(
            (ad for ad in published_ads_list if ad_matches_id(ad, ad_cfg.id)), None
        )
        if not published_ad:
            LOG.warning(" -> SKIPPED: ad '%s' (ID: %s) not found in published ads", ad_cfg.title, ad_cfg.id)
            continue

        # Skip non-active ads
        if published_ad.get("state") != "active":
            LOG.info(" -> SKIPPED: ad '%s' is not active (state: %s)", ad_cfg.title, published_ad.get("state"))
            continue

        # Check if ad is within 8-day extension window using API's endDate
        end_date_str = published_ad.get("endDate")
        if not end_date_str:
            LOG.warning(" -> SKIPPED: ad '%s' has no endDate in API response", ad_cfg.title)
            continue

        # Intentionally parsing naive datetime from kleinanzeigen API's German date format, timezone not relevant for date-only comparison
        try:
            end_date = datetime.strptime(end_date_str, "%d.%m.%Y")  # noqa: DTZ007
        except (ValueError, TypeError):
            LOG.warning(" -> SKIPPED: ad '%s' has invalid endDate format: %s", ad_cfg.title, end_date_str)
            continue
        days_until_expiry = (end_date.date() - _misc.now().date()).days

        # Magic value 8 is kleinanzeigen.de's platform policy: extensions only possible within 8 days of expiry
        if days_until_expiry <= 8:  # noqa: PLR2004
            LOG.info(" -> ad '%s' expires in %d days, will extend", ad_cfg.title, days_until_expiry)
            ads_to_extend.append((ad_file, ad_cfg, ad_cfg_orig))
        else:
            LOG.info(" -> SKIPPED: ad '%s' expires in %d days (can only extend within 8 days)", ad_cfg.title, days_until_expiry)

    if not ads_to_extend:
        LOG.info("No ads need extension at this time.")
        LOG.info("############################################")
        LOG.info("DONE: No ads extended.")
        LOG.info("############################################")
        return

    async def process_ad(entry:bulk_ad_actions.AdEntry, page_num:int) -> bool:
        ad_file, ad_cfg, ad_cfg_orig = entry
        return await _extend_ad(web, ad_file, ad_cfg, ad_cfg_orig, page_num = page_num)

    def report_missing(ad_cfg:Ad) -> None:
        LOG.error(" -> FAILED: Could not find extend button for ad ID %s", ad_cfg.id)

    success_count = await bulk_ad_actions.process_ads(
        web, root_url, ads_to_extend, process_ad = process_ad, report_missing = report_missing
    )

    LOG.info("############################################")
    LOG.info("DONE: Extended %s", pluralize("ad", success_count))
    LOG.info("############################################")


async def _extend_ad(
    web:WebScrapingMixin,
    ad_file:str,
    ad_cfg:Ad,
    ad_cfg_orig:dict[str, Any],
    *,
    page_num:int,
) -> bool:
    """Extends a single ad listing."""
    LOG.info("Extending ad '%s' (ID: %s)...", ad_cfg.title, ad_cfg.id)

    try:
        if not await _click_extend_button(web, ad_cfg.id, page_num = page_num):
            return False

        # Preserve the existing local command's metadata behavior.
        ad_cfg_orig["updated_on"] = _misc.now().isoformat(timespec = "seconds")
        _dicts.save_dict(ad_file, ad_cfg_orig)
        LOG.info(" -> SUCCESS: ad extended with ID %s", ad_cfg.id)
        return True
    except OSError as ex:
        LOG.error(" -> FAILED: Could not persist extension for ad '%s': %s", ad_cfg.title, ex)
        return False


async def _click_extend_button(web:WebScrapingMixin, ad_id:int | None, *, page_num:int) -> bool:
    """Perform the free extension action and dismiss the optional paid promotion."""
    try:
        extend_button_xpath = f'//li[@data-adid="{ad_id}"]//button[contains(., "Verlängern")]'
        try:
            extend_button = await web.web_find(By.XPATH, extend_button_xpath, timeout = web.timeout("quick_dom"))
        except TimeoutError:
            LOG.error(" -> FAILED: Could not find extend button for ad ID %s", ad_id)
            return False
        LOG.info("Found extend button on page %s", page_num)
        await extend_button.click()

        # Handle confirmation dialog
        # After clicking "Verlängern", a dialog appears with:
        # - Title: "Vielen Dank!"
        # - Message: "Deine Anzeige ... wurde erfolgreich verlängert."
        # - Paid bump-up option (skipped by closing dialog)
        # Simply close the dialog with the X button (aria-label="Schließen")
        try:
            dialog_close_timeout = web.timeout("quick_dom")
            await web.web_click(By.CSS_SELECTOR, 'button[aria-label="Schließen"]', timeout = dialog_close_timeout)
            LOG.debug(" -> Closed confirmation dialog")
        except TimeoutError:
            LOG.warning(" -> No confirmation dialog found, extension may have completed directly")

        return True

    except TimeoutError as ex:
        LOG.error(" -> FAILED: Timeout while extending ad ID %s: %s", ad_id, ex)
        return False


def _online_end_date(ad:PublishedAd) -> date | None:
    """Read the date-only API value without guessing an expiry time."""
    try:
        return datetime.strptime(ad.get("endDate", ""), "%d.%m.%Y").date()  # noqa: DTZ007
    except (TypeError, ValueError):
        LOG.warning(" -> SKIPPED: ad ID %s has invalid or missing endDate: %s", ad.get("id"), ad.get("endDate"))
        return None


def select_online_ads(ads:list[PublishedAd], *, today:date, selector:str) -> dict[int, tuple[PublishedAd, date]]:
    """Select unique, active account ads in the inclusive eight-day window."""
    selected:dict[int, tuple[PublishedAd, date]] = {}
    requested = None if selector == "all" else {int(value.strip()) for value in selector.split(",")}
    for ad in ads:
        raw_id = ad.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            continue
        try:
            ad_id = int(raw_id)
        except ValueError:
            continue
        if ad_id <= 0 or ad.get("state") != "active" or (requested is not None and ad_id not in requested):
            continue
        end_date = _online_end_date(ad)
        if end_date is not None and 0 <= (end_date - today).days <= 8:  # noqa: PLR2004
            selected.setdefault(ad_id, (ad, end_date))
    return selected


async def extend_online_ads(web:WebScrapingMixin, root_url:str, *, selector:str = "all", dry_run:bool = False) -> None:
    """Extend account listings without loading or writing local ad configurations."""
    ads = await published_ads.fetch_published_ads(web, root_url, strict = True)
    today = _misc.now().astimezone(ZoneInfo("Europe/Berlin")).date()
    selected = select_online_ads(ads, today = today, selector = selector)
    for ad_id, (ad, end_date) in selected.items():
        LOG.info("Selected online ad ID %s: '%s', expires %s", ad_id, ad.get("title", ""), end_date)
    if dry_run:
        LOG.info("DRY RUN: %d online ads eligible for extension; no ads changed.", len(selected))
        return
    if not selected:
        LOG.info("No ads need extension at this time.")
        return

    pending = set(selected)
    clicked:set[int] = set()

    async def process_page(page_num:int) -> bool:
        rows = await web.web_find_all(By.CSS_SELECTOR, "#my-manageitems-adlist li[data-adid]", timeout = web.timeout("quick_dom"))
        page_ids = [str(row.attrs.get("data-adid")) for row in rows]
        for raw_id in page_ids:
            if not raw_id.isdecimal() or (ad_id := int(raw_id)) not in pending:
                continue
            pending.remove(ad_id)
            LOG.info("Extending online ad ID %s: '%s'...", ad_id, selected[ad_id][0].get("title", ""))
            try:
                if await _click_extend_button(web, ad_id, page_num = page_num):
                    clicked.add(ad_id)
            except (OSError, RuntimeError) as ex:
                LOG.error(" -> FAILED: Could not extend online ad ID %s: %s", ad_id, ex)
            await web.web_sleep()
        return not pending

    await web.navigate_paginated_ad_overview(process_page, page_url = f"{root_url}/m-meine-anzeigen.html")
    # A click alone is not proof: require a later expiry date from a fresh API read.
    refreshed = await published_ads.fetch_published_ads(web, root_url, strict = True)
    confirmed:set[int] = set()
    for ad in refreshed:
        for ad_id in clicked - confirmed:
            if ad_matches_id(ad, ad_id) and (new_date := _online_end_date(ad)) is not None and new_date > selected[ad_id][1]:
                confirmed.add(ad_id)
                LOG.info(" -> SUCCESS: online ad ID %s extended until %s", ad_id, new_date)
    failed = set(selected) - confirmed
    LOG.info("DONE: Confirmed %d online extensions; %d failed.", len(confirmed), len(failed))
    if failed:
        LOG.error("Unconfirmed online extension IDs: %s", ", ".join(map(str, sorted(failed))))
        raise AdBatchError(_("Some online extensions could not be confirmed."))
