# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the extend command and extend_flow module."""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from kleinanzeigen_bot import extend_flow, runtime_config
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.published_ads import PublishedAdsFetchIncompleteError
from kleinanzeigen_bot.utils import dicts, misc, xdg_paths
from kleinanzeigen_bot.utils.exceptions import AdBatchError


@pytest.fixture
def base_ad_config_with_id() -> dict[str, Any]:
    """Provide a base ad configuration with an ID for extend tests."""
    return {
        "id": 12345,
        "title": "Test Ad Title",
        "description": "Test Description",
        "type": "OFFER",
        "price_type": "FIXED",
        "price": 100,
        "shipping_type": "SHIPPING",
        "shipping_options": [],
        "category": "160",
        "special_attributes": {},
        "sell_directly": False,
        "images": [],
        "active": True,
        "republication_interval": 7,
        "created_on": "2024-12-07T10:00:00",
        "updated_on": "2024-12-10T15:20:00",
        "contact": {"name": "Test User", "zipcode": "12345", "location": "Test City", "street": "", "phone": ""},
    }


class TestExtendCommand:
    """Tests for the extend command functionality."""

    @pytest.mark.asyncio
    async def test_run_extend_command_no_ads(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Test running extend command with no ads."""
        test_bot.config_file_path = str(tmp_path / "config.yaml")
        workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        with (
            patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
            patch(
                "kleinanzeigen_bot.runtime_config.load_config",
                return_value = runtime_config.RuntimeState(config = test_bot.config, categories = {}, timing_collector = None),
            ),
            patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
            patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
            patch.object(test_bot, "load_ads", return_value = []),
            patch("kleinanzeigen_bot.update_checker.UpdateChecker"),
        ):
            await test_bot.run(["script.py", "extend"])
            assert test_bot.command == "extend"
            assert test_bot.ads_selector == "all"

    @pytest.mark.asyncio
    async def test_run_extend_command_with_specific_ids(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Test running extend command with specific ad IDs."""
        test_bot.config_file_path = str(tmp_path / "config.yaml")
        workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        with (
            patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
            patch(
                "kleinanzeigen_bot.runtime_config.load_config",
                return_value = runtime_config.RuntimeState(config = test_bot.config, categories = {}, timing_collector = None),
            ),
            patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
            patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
            patch.object(test_bot, "load_ads", return_value = []),
            patch.object(test_bot, "create_browser_session", new_callable = AsyncMock),
            patch.object(test_bot, "login", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.update_checker.UpdateChecker"),
        ):
            await test_bot.run(["script.py", "extend", "--ads=12345,67890"])
            assert test_bot.command == "extend"
            assert test_bot.ads_selector == "12345,67890"

    @pytest.mark.asyncio
    async def test_online_command_does_not_load_local_ads(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        with (
            patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
            patch(
                "kleinanzeigen_bot.runtime_config.load_config",
                return_value = runtime_config.RuntimeState(config = test_bot.config, categories = {}, timing_collector = None),
            ),
            patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
            patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
            patch.object(test_bot, "load_ads") as load,
            patch.object(test_bot, "_open_logged_in_browser", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.update_checker.UpdateChecker"),
            patch("kleinanzeigen_bot.extend_flow.extend_online_ads", new_callable = AsyncMock) as extend,
        ):
            await test_bot.run(["script.py", "extend", "--online", "--dry-run"])
            load.assert_not_called()
            extend.assert_awaited_once_with(test_bot, test_bot.root_url, selector = "all", dry_run = True)


class TestExtendAdsMethod:
    """Tests for the extend_ads() method."""

    @pytest.fixture(autouse = True)
    def overview(self, test_bot:KleinanzeigenBot) -> Any:
        async def navigate(callback:Callable[[int], Awaitable[bool]], page_url:str) -> bool:  # noqa: ARG001
            return await callback(1)

        rows = [MagicMock(attrs = {"data-adid": str(ad_id)}) for ad_id in (12345, 67890)]
        with (
            patch.object(test_bot, "navigate_paginated_ad_overview", side_effect = navigate),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = rows),
        ):
            yield

    @pytest.mark.asyncio
    async def test_extend_ads_skips_unpublished_ad(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads skips ads without an ID (unpublished)."""
        # Create ad without ID
        ad_config = base_ad_config_with_id.copy()
        ad_config["id"] = None
        ad_cfg = Ad.model_validate(ad_config)

        with patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request, patch.object(test_bot, "web_sleep", new_callable = AsyncMock):
            mock_request.return_value = {"content": '{"ads": []}'}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, ad_config)])

            # Verify no extension was attempted
            mock_request.assert_called_once()  # Only the API call to get published ads

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_not_in_published_list(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads skips ads not found in the published ads API response."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request, patch.object(test_bot, "web_sleep", new_callable = AsyncMock):
            # Return empty published ads list
            mock_request.return_value = {"content": '{"ads": []}'}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify no extension was attempted
            mock_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ads_skips_inactive_ad(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads skips ads with state != 'active'."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        published_ads_json = {
            "ads": [
                {
                    "id": 12345,
                    "title": "Test Ad Title",
                    "state": "paused",  # Not active
                    "endDate": "05.02.2026",
                }
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_without_enddate(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads skips ads without endDate in API response."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        published_ads_json = {
            "ads": [
                {
                    "id": 12345,
                    "title": "Test Ad Title",
                    "state": "active",
                    # No endDate field
                }
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_outside_window(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads skips ads expiring more than 8 days in the future."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to 30 days from now (outside 8-day window)
        future_date = misc.now() + timedelta(days = 30)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_extends_ad_within_window(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads extends ads within the 8-day window."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to 5 days from now (within 8-day window)
        future_date = misc.now() + timedelta(days = 5)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was called
            mock_extend_ad.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ads_handles_multiple_ads(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads processes multiple ads correctly."""
        ad_cfg1 = Ad.model_validate(base_ad_config_with_id)

        # Create second ad
        ad_config2 = base_ad_config_with_id.copy()
        ad_config2["id"] = 67890
        ad_config2["title"] = "Second Test Ad"
        ad_cfg2 = Ad.model_validate(ad_config2)

        # Set end dates - one within window, one outside
        within_window = misc.now() + timedelta(days = 5)
        outside_window = misc.now() + timedelta(days = 30)

        published_ads_json = {
            "ads": [
                {"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": within_window.strftime("%d.%m.%Y")},
                {"id": 67890, "title": "Second Test Ad", "state": "active", "endDate": outside_window.strftime("%d.%m.%Y")},
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await extend_flow.extend_ads(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfgs = [("test1.yaml", ad_cfg1, base_ad_config_with_id), ("test2.yaml", ad_cfg2, ad_config2)],
            )

            # Verify extend_ad was called only once (for the ad within window)
            assert mock_extend_ad.call_count == 1

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_with_invalid_enddate(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads gracefully skips ads with invalid endDate format instead of crashing."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        published_ads_json = {
            "ads": [
                {
                    "id": 12345,
                    "title": "Test Ad Title",
                    "state": "active",
                    "endDate": "not-a-date",  # Invalid format
                }
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            # Should not raise — gracefully skips invalid endDate
            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was NOT called (invalid endDate → skip)
            mock_extend_ad.assert_not_called()


class TestExtendAdMethod:
    """Tests for current-page button handling and extension persistence."""

    @pytest.mark.asyncio
    async def test_extend_ad_success(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path) -> None:
        """Test successful ad extension."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.utils.misc.now") as mock_now,
        ):
            # Test mock datetime - timezone not relevant for timestamp formatting test
            mock_now.return_value = datetime(2025, 1, 28, 14, 30, 0)  # noqa: DTZ001

            mock_find.return_value = AsyncMock()

            result = await extend_flow._extend_ad(
                web = test_bot, page_num = 1,
                ad_file = str(ad_file), ad_cfg = ad_cfg,
                ad_cfg_orig = base_ad_config_with_id,
            )

            assert result is True
            assert mock_find.call_count == 1

            # Verify updated_on was updated in the YAML file
            updated_config = dicts.load_dict(str(ad_file))
            assert updated_config["updated_on"] == "2025-01-28T14:30:00"

    @pytest.mark.asyncio
    async def test_extend_ad_button_not_found(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path) -> None:
        """Test _extend_ad when the Verlängern button is not found."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find:
            # The current row has no extend button.
            mock_find.side_effect = TimeoutError("Button not found")

            result = await extend_flow._extend_ad(
                web = test_bot, page_num = 1,
                ad_file = str(ad_file), ad_cfg = ad_cfg,
                ad_cfg_orig = base_ad_config_with_id,
            )

            assert result is False
            assert mock_find.call_count == 1

    @pytest.mark.asyncio
    async def test_extend_ad_dialog_timeout(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path) -> None:
        """Test _extend_ad when the confirmation dialog times out (no dialog appears)."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch("kleinanzeigen_bot.utils.misc.now") as mock_now,
        ):
            # Test mock datetime - timezone not relevant for timestamp formatting test
            mock_now.return_value = datetime(2025, 1, 28, 14, 30, 0)  # noqa: DTZ001

            # The extend button is available.
            mock_find.return_value = AsyncMock()
            # Dialog close button times out
            mock_click.side_effect = TimeoutError("Dialog not found")

            result = await extend_flow._extend_ad(
                web = test_bot, page_num = 1,
                ad_file = str(ad_file), ad_cfg = ad_cfg,
                ad_cfg_orig = base_ad_config_with_id,
            )

            # Should still succeed (dialog might not appear)
            assert result is True

    @pytest.mark.asyncio
    async def test_extend_ad_exception_handling(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path) -> None:
        """Test _extend_ad propagates unexpected exceptions."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find:
            # Unexpected browser errors must propagate.
            mock_find.side_effect = Exception("Unexpected error")

            with pytest.raises(Exception, match = "Unexpected error"):
                await extend_flow._extend_ad(
                    web = test_bot, page_num = 1,
                    ad_file = str(ad_file), ad_cfg = ad_cfg,
                    ad_cfg_orig = base_ad_config_with_id,
                )


class TestExtendEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.fixture(autouse = True)
    def overview(self, test_bot:KleinanzeigenBot) -> Any:
        async def navigate(callback:Callable[[int], Awaitable[bool]], page_url:str) -> bool:  # noqa: ARG001
            return await callback(1)

        rows = [MagicMock(attrs = {"data-adid": str(ad_id)}) for ad_id in (12345, 67890)]
        with (
            patch.object(test_bot, "navigate_paginated_ad_overview", side_effect = navigate),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = rows),
        ):
            yield

    @pytest.mark.asyncio
    async def test_extend_ads_exactly_8_days(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that ads expiring exactly in 8 days are eligible for extension."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to exactly 8 days from now (boundary case)
        future_date = misc.now() + timedelta(days = 8)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was called (8 days is within the window)
            mock_extend_ad.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ads_exactly_9_days(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that ads expiring in exactly 9 days are not eligible for extension."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to exactly 9 days from now (just outside window)
        future_date = misc.now() + timedelta(days = 9)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called (9 days is outside the window)
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_date_parsing_german_format(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads correctly parses German date format (DD.MM.YYYY)."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Use a specific German date format
        published_ads_json = {
            "ads": [
                {
                    "id": 12345,
                    "title": "Test Ad Title",
                    "state": "active",
                    "endDate": "05.02.2026",  # German format: DD.MM.YYYY
                }
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
            patch("kleinanzeigen_bot.utils.misc.now") as mock_now,
        ):
            # Mock now() to return a date where 05.02.2026 would be within 8 days
            # Test mock datetime - timezone not relevant for date comparison test
            mock_now.return_value = datetime(2026, 1, 28)  # noqa: DTZ001
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was called (date was parsed correctly)
            mock_extend_ad.assert_called_once()


class TestOnlineExtension:
    """Account mode is independent of local ad configuration and confirms results."""

    @pytest.mark.parametrize("offset", [-1, 0, 8, 9])
    def test_date_window(self, offset:int) -> None:
        today = datetime(2026, 10, 15).date()  # noqa: DTZ001
        ad = {"id": "1", "state": "active", "endDate": (today + timedelta(days = offset)).strftime("%d.%m.%Y")}
        selected = extend_flow.select_online_ads([ad, ad], today = today, selector = "all")
        assert list(selected) == ([1] if 0 <= offset <= 8 else [])  # noqa: PLR2004

    def test_invalid_and_inactive_ads(self) -> None:
        today = datetime(2026, 10, 15).date()  # noqa: DTZ001
        ads = [{"id": value, "state": "active", "endDate": "15.10.2026"} for value in [None, True, -1, "bad", 1.2]]
        ads += [{"id": 1, "state": state, "endDate": "15.10.2026"} for state in ["paused", "inactive"]]
        ads += [{"id": 2, "state": "active", "endDate": value} for value in [None, "bad", 123]]
        assert not extend_flow.select_online_ads(ads, today = today, selector = "all")

    def test_numeric_selector(self) -> None:
        today = datetime(2026, 10, 15).date()  # noqa: DTZ001
        ads = [{"id": value, "state": "active", "endDate": "15.10.2026"} for value in [1, 2]]
        assert list(extend_flow.select_online_ads(ads, today = today, selector = "2")) == [2]

    @pytest.mark.asyncio
    async def test_dry_run_uses_berlin_date(self, test_bot:KleinanzeigenBot) -> None:
        # Berlin is already on the 16th; the UTC date is still the 15th.
        ads = [{"id": 1, "state": "active", "endDate": "24.10.2026"}]
        with (
            patch("kleinanzeigen_bot.extend_flow.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = ads),
            patch("kleinanzeigen_bot.extend_flow._misc.now", return_value = datetime(2026, 10, 15, 23, tzinfo = UTC)),
            patch.object(test_bot, "navigate_paginated_ad_overview", new_callable = AsyncMock) as navigate,
            patch("kleinanzeigen_bot.extend_flow.LOG.info") as info,
        ):
            await extend_flow.extend_online_ads(test_bot, test_bot.root_url, dry_run = True)
            navigate.assert_not_called()
            assert any(call.args[-1] == 1 for call in info.call_args_list)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("confirmed", [True, False])
    async def test_confirms_expiry_and_reports_partial_failure(self, test_bot:KleinanzeigenBot, confirmed:bool) -> None:
        today = misc.now().astimezone(ZoneInfo("Europe/Berlin")).date()
        ads = [{"id": value, "state": "active", "endDate": today.strftime("%d.%m.%Y")} for value in [1, 2]]
        after = [dict(ad, endDate = (today + timedelta(days = 60 if confirmed or ad["id"] == 1 else 0)).strftime("%d.%m.%Y")) for ad in ads]

        async def navigate(callback:Callable[[int], Awaitable[bool]], page_url:str) -> None:  # noqa: ARG001
            for page in [1, 2]:
                if await callback(page):
                    break

        with (
            patch("kleinanzeigen_bot.extend_flow.published_ads.fetch_published_ads", new_callable = AsyncMock, side_effect = [ads, after]),
            patch.object(test_bot, "navigate_paginated_ad_overview", side_effect = navigate),
            patch.object(
                test_bot, "web_find_all", new_callable = AsyncMock,
                side_effect = [[MagicMock(attrs = {"data-adid": "1"})], [MagicMock(attrs = {"data-adid": "2"})]],
            ),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = AsyncMock()),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._dicts.save_dict") as save,
        ):
            if confirmed:
                await extend_flow.extend_online_ads(test_bot, test_bot.root_url)
            else:
                with pytest.raises(AdBatchError):
                    await extend_flow.extend_online_ads(test_bot, test_bot.root_url)
            save.assert_not_called()

    @pytest.mark.asyncio
    async def test_incomplete_fetch_never_clicks(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch(
                "kleinanzeigen_bot.extend_flow.published_ads.fetch_published_ads",
                new_callable = AsyncMock, side_effect = PublishedAdsFetchIncompleteError("incomplete"),
            ),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as find,
        ):
            with pytest.raises(PublishedAdsFetchIncompleteError):
                await extend_flow.extend_online_ads(test_bot, test_bot.root_url)
            find.assert_not_called()
