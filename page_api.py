# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import copy
import datetime
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.web import request


PAGE_API_PREFIX = "/astrbot_plugin_point_system/page"
MEDIA_UPLOAD_MAX_BYTES = 50 * 1024 * 1024
MEDIA_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"},
    "video": {".mp4", ".webm", ".mov", ".m4v"},
}

SETTINGS_DEFAULTS: dict[str, Any] = {
    "points_name": "积分",
    "sign_in_trigger_keyword": "今日",
    "lottery_trigger_keyword": "今日",
    "sign_in_settings": {
        "sign_in_mode": "random",
        "fixed_sign_in_points": 20,
        "min_sign_in_points": 5,
        "max_sign_in_points": 25,
        "first_sign_in_bonus": 50,
        "daily_first_sign_in_bonus": 20,
        "fortune_event_enabled": True,
        "fortune_event_chance": 0.002,
        "fortune_event_points": 300,
        "streak_bonus_enabled": True,
        "streak_step_bonus": 2,
        "streak_bonus_cap": 10,
        "weekly_streak_bonus": 10,
        "make_up_cost": 100,
        "make_up_monthly_limit": 1,
    },
    "activity_settings": {
        "enabled": False,
        "points_per_message": 1,
        "cooldown_seconds": 300,
        "daily_limit": 6,
        "min_text_length": 4,
    },
    "steal_settings": {
        "enabled": False,
        "daily_steal_limit": 3,
        "daily_be_stolen_limit": 3,
        "min_points": 1,
        "max_points": 20,
        "success_probability": 0.5,
        "failure_cost": 0,
        "failure_cost_to_victim": True,
    },
    "birthday_settings": {
        "enabled": True,
        "reward_points": 50,
        "auto_record_when_unset": True,
        "auto_broadcast_enabled": True,
        "auto_broadcast_time": "08:00",
    },
    "lottery_settings": {
        "enabled": True,
        "default_mode": "personal",
        "personal_enabled": True,
        "group_enabled": True,
        "personal_cost": 20,
        "personal_daily_limit": 1,
        "group_cost": 20,
        "group_daily_limit_per_user": 1,
        "group_required_participants": 5,
    },
    "leaderboard_settings": {
        "display_limit": 10,
        "show_self_rank": True,
    },
    "red_packet_settings": {
        "enabled": True,
        "max_total_points": 100000,
        "max_count": 100,
        "expire_minutes": 1440,
    },
    "exchange_settings": {
        "title_enabled": True,
        "title_cost": 500,
        "title_max_length": 6,
        "essence_enabled": True,
        "essence_cost": 500,
        "mute_enabled": True,
        "mute_cost": 1000,
        "mute_duration_seconds": 60,
        "allow_mute_others": False,
    },
    "backup_settings": {
        "enabled": False,
        "backup_paths": [],
        "auto_backup_time": "03:00",
    },
    "admin_settings": {
        "log_operations": True,
        "max_admin_give": 1000,
        "points_admin_ids": [],
    },
    "negative_settings": {
        "debt_message": "你已背负债务，请穿上女仆装打工。",
    },
}

SETTINGS_SELECTS = {
    "sign_in_settings.sign_in_mode": {"random", "fixed"},
    "lottery_settings.default_mode": {"personal", "group"},
}

SETTINGS_MINIMUMS = {
    "sign_in_settings.fixed_sign_in_points": 0,
    "sign_in_settings.min_sign_in_points": 0,
    "sign_in_settings.max_sign_in_points": 0,
    "sign_in_settings.first_sign_in_bonus": 0,
    "sign_in_settings.daily_first_sign_in_bonus": 0,
    "sign_in_settings.fortune_event_points": 0,
    "sign_in_settings.streak_step_bonus": 0,
    "sign_in_settings.streak_bonus_cap": 0,
    "sign_in_settings.weekly_streak_bonus": 0,
    "sign_in_settings.make_up_cost": 0,
    "sign_in_settings.make_up_monthly_limit": 0,
    "activity_settings.cooldown_seconds": 0,
    "activity_settings.daily_limit": 0,
    "steal_settings.daily_steal_limit": 0,
    "steal_settings.daily_be_stolen_limit": 0,
    "steal_settings.min_points": 1,
    "steal_settings.max_points": 1,
    "steal_settings.failure_cost": 0,
    "birthday_settings.reward_points": 0,
    "red_packet_settings.expire_minutes": 0,
}


class PointSystemPageApi:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._settings_lock = asyncio.Lock()
        self._registered_routes: list[tuple[str, Any]] = []

    def register_routes(self) -> None:
        register = getattr(self.plugin.context, "register_web_api", None)
        if not callable(register):
            logger.warning("[PointSystem] 当前 AstrBot 不支持插件拓展页 API")
            return

        routes = [
            ("/overview", self.overview, ["GET"], "Point System exchange overview"),
            ("/dashboard", self.dashboard, ["GET"], "Point System filtered dashboard"),
            ("/media/upload", self.upload_media, ["POST"], "Point System media upload"),
            ("/items/save", self.save_items, ["POST"], "Point System save exchange items"),
            ("/settings/save", self.save_settings, ["POST"], "Point System save common settings"),
        ]
        registered = getattr(self.plugin.context, "registered_web_apis", None)
        snapshot = list(registered) if isinstance(registered, list) else None
        try:
            for path, handler, methods, description in routes:
                route = f"{PAGE_API_PREFIX}{path}"
                register(route, handler, methods, description)
                self._registered_routes.append((route, handler))
        except Exception:
            if snapshot is not None:
                registered[:] = snapshot
                self._registered_routes.clear()
            else:
                self.unregister_routes()
            raise

    def unregister_routes(self) -> None:
        registered = getattr(self.plugin.context, "registered_web_apis", None)
        if not isinstance(registered, list) or not self._registered_routes:
            return
        owned = {(route, id(handler)) for route, handler in self._registered_routes}
        registered[:] = [
            item
            for item in registered
            if not (
                isinstance(item, tuple)
                and len(item) >= 2
                and (item[0], id(item[1])) in owned
            )
        ]
        self._registered_routes.clear()

    @staticmethod
    async def _payload() -> dict[str, Any]:
        value = await request.json(default={})
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return str(value or "").strip()[:limit]

    @staticmethod
    def _request_query_value(name: str, default: Any = None) -> Any:
        """Read a query value across AstrBot request API generations."""
        for attribute in ("query", "args"):
            try:
                values = getattr(request, attribute, None)
                getter = getattr(values, "get", None)
                if callable(getter):
                    return getter(name, default)
            except (AttributeError, RuntimeError):
                continue
        return default

    @staticmethod
    def _bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return default

    @staticmethod
    def _int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(int(value), maximum))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            return max(minimum, min(float(value), maximum))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _date_text(value: Any) -> str:
        text = str(value or "").strip()
        return text[:10] if len(text) >= 10 else text

    def _item_repeatable(self, item: dict[str, Any]) -> bool:
        checker = getattr(self.plugin, "_exchange_item_repeatable", None)
        return bool(checker(item)) if callable(checker) else bool(item.get("repeatable", False))

    @staticmethod
    def _selection_mode(value: Any) -> str:
        return "random" if str(value or "").strip().casefold() in {"random", "rand", "随机"} else "sequential"

    def _content_type(self, value: Any, contents: list[str]) -> str:
        checker = getattr(self.plugin, "_exchange_content_type", None)
        if callable(checker):
            return checker(value, contents)
        normalized = str(value or "").strip().casefold()
        if normalized in {"text", "image", "video"}:
            return normalized
        detected = {self._media_kind(content) for content in contents}
        detected.discard("")
        return next(iter(detected)) if len(detected) == 1 else "text"

    def _media_kind(self, content: Any) -> str:
        checker = getattr(self.plugin, "_exchange_content_media", None)
        if callable(checker):
            return str(checker(content)[0] or "").casefold()
        match = re.match(r"^(image|video)\s*(?:://|:|\|)\s*.+$", str(content or "").strip(), re.IGNORECASE)
        return match.group(1).casefold() if match else ""

    def _redemption_consumes_stock(self, record: dict[str, Any]) -> bool:
        checker = getattr(self.plugin, "_exchange_redemption_consumes_stock", None)
        return bool(checker(record)) if callable(checker) else not bool(record.get("repeatable", False))

    def _settings_view(self) -> dict[str, Any]:
        result = copy.deepcopy(SETTINGS_DEFAULTS)
        for key, default in SETTINGS_DEFAULTS.items():
            current = self.plugin.config.get(key, default)
            if isinstance(default, dict):
                if not isinstance(current, dict):
                    continue
                for child_key in default:
                    if child_key in current:
                        result[key][child_key] = copy.deepcopy(current[child_key])
            elif key in self.plugin.config:
                result[key] = copy.deepcopy(current)
        return result

    def _validate_settings(self, raw_settings: Any) -> tuple[dict[str, Any], str]:
        if not isinstance(raw_settings, dict):
            return {}, "配置数据格式不正确"

        current = self._settings_view()
        result = copy.deepcopy(current)
        for key, default in SETTINGS_DEFAULTS.items():
            if key not in raw_settings:
                continue
            raw_value = raw_settings[key]
            if isinstance(default, dict):
                if not isinstance(raw_value, dict):
                    return {}, f"{key} 配置格式不正确"
                for child_key, child_default in default.items():
                    if child_key not in raw_value:
                        continue
                    path = f"{key}.{child_key}"
                    value = raw_value[child_key]
                    if path in SETTINGS_SELECTS:
                        normalized = str(value or "").strip().casefold()
                        result[key][child_key] = (
                            normalized
                            if normalized in SETTINGS_SELECTS[path]
                            else child_default
                        )
                    elif isinstance(child_default, bool):
                        result[key][child_key] = self._bool(value, child_default)
                    elif isinstance(child_default, int):
                        minimum = SETTINGS_MINIMUMS.get(path, 1)
                        result[key][child_key] = self._int(
                            value, child_default, minimum, 1_000_000_000
                        )
                    elif isinstance(child_default, float):
                        result[key][child_key] = self._float(
                            value, child_default, 0.0, 1.0
                        )
                    elif isinstance(child_default, list):
                        values = value if isinstance(value, list) else str(value or "").splitlines()
                        result[key][child_key] = [
                            self._text(item, 500) for item in values if self._text(item, 500)
                        ][:1000]
                    else:
                        result[key][child_key] = self._text(value, 4000)
            elif isinstance(default, str):
                result[key] = self._text(raw_value, 120)

        if not result["points_name"]:
            return {}, "积分名称不能为空"
        return result, ""

    def _config_revision(self) -> str:
        raw_items = self.plugin.config.get("exchange_items", [])
        serialized = json.dumps(
            {
                "items": raw_items,
                "scope": self._scope_view(),
                "settings": self._settings_view(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]

    def _validate_scope(self, raw_scope: Any) -> tuple[dict[str, Any], str]:
        if not isinstance(raw_scope, dict):
            raw_scope = {}

        mode_value = str(raw_scope.get("mode") or "").strip().casefold()
        mode = (
            "whitelist"
            if mode_value in {"whitelist", "white", "allow", "白名单", "允许"}
            else "blacklist"
        )
        raw_values = raw_scope.get("scope", raw_scope.get("group_ids", []))
        if isinstance(raw_values, str):
            values = (
                raw_values.replace("，", ",")
                .replace("\r", "\n")
                .replace(",", "\n")
                .splitlines()
            )
        elif isinstance(raw_values, list):
            values = raw_values
        else:
            values = []

        scope: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            value = str(raw_value or "").strip()[:120]
            if not value:
                continue
            normalized = value.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            scope.append(value)
        if len(scope) > 1000:
            return {}, "兑换适用范围最多配置 1000 个群号或账号"
        return {"mode": mode, "scope": scope}, ""

    def _scope_view(self) -> dict[str, Any]:
        scope, _ = self._validate_scope(
            self.plugin.config.get("exchange_scope", {})
        )
        return scope

    def _redemption_view(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        raw_status = self._text(value.get("delivery_status"), 32).casefold()
        return {
            "item_name": self._text(value.get("item_name"), 120),
            "user_id": self._text(value.get("user_id"), 120),
            "group_id": self._text(value.get("group_id"), 120),
            "redeemed_at": self._text(value.get("redeemed_at"), 64),
            "cost": self._int(value.get("cost"), 0, 0, 1_000_000_000),
            "delivery_status": (
                "uncertain"
                if raw_status in {"pending", "uncertain"}
                else "delivered"
            ),
        }

    def _group_option_views(
        self, users: dict[Any, Any], groups: dict[Any, Any]
    ) -> list[dict[str, Any]]:
        normalized_users = {
            str(user_id): record
            for user_id, record in users.items()
            if isinstance(record, dict)
        }
        options: list[dict[str, Any]] = []
        for raw_group_id, raw_group in groups.items():
            if not isinstance(raw_group, dict):
                continue
            members = raw_group.get("members", {})
            member_ids = (
                {str(user_id) for user_id in members}
                if isinstance(members, dict)
                else set()
            )
            balances = [
                self._int(
                    normalized_users[user_id].get("points"),
                    0,
                    -1_000_000_000,
                    1_000_000_000,
                )
                for user_id in member_ids
                if user_id in normalized_users
            ]
            options.append(
                {
                    "group_id": str(raw_group_id),
                    "member_count": len(member_ids),
                    "tracked_count": len(balances),
                    "total_points": sum(balances),
                }
            )
        return sorted(
            options, key=lambda item: item["total_points"], reverse=True
        )

    def _point_history_view(
        self, group_id: str = "", history_range: str = "7d"
    ) -> dict[str, Any]:
        range_days = {"24h": 1, "7d": 7, "30d": 30}
        selected_range = history_range if history_range in range_days else "7d"
        bucket_minutes = {"24h": 30, "7d": 120, "30d": 480}[selected_range]
        cutoff = datetime.datetime.now() - datetime.timedelta(
            days=range_days[selected_range]
        )
        snapshots = self.plugin.data.get("point_snapshots", [])
        if not isinstance(snapshots, list):
            snapshots = []

        parsed: list[tuple[datetime.datetime, dict[str, Any]]] = []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            captured_text = self._text(snapshot.get("captured_at"), 64)
            try:
                captured_at = datetime.datetime.fromisoformat(captured_text)
            except (TypeError, ValueError):
                continue
            if captured_at.tzinfo is not None:
                captured_at = captured_at.astimezone().replace(tzinfo=None)
            if group_id:
                snapshot_groups = snapshot.get("groups", {})
                group_snapshot = (
                    snapshot_groups.get(group_id)
                    if isinstance(snapshot_groups, dict)
                    else None
                )
                if not isinstance(group_snapshot, dict):
                    continue
                source = group_snapshot
            else:
                source = snapshot
            parsed.append(
                (
                    captured_at,
                    {
                        "captured_at": captured_at.isoformat(timespec="seconds"),
                        "total_points": self._int(
                            source.get("total_points"),
                            0,
                            -1_000_000_000_000,
                            1_000_000_000_000,
                        ),
                        "user_count": self._int(
                            source.get("user_count"), 0, 0, 100_000_000
                        ),
                    },
                )
            )

        parsed.sort(key=lambda item: item[0])
        visible = [item for item in parsed if item[0] >= cutoff]
        before_range = [item for item in parsed if item[0] < cutoff]
        if before_range:
            visible.insert(0, before_range[-1])

        bucket_seconds = bucket_minutes * 60
        bucketed: dict[int, dict[str, Any]] = {}
        for captured_at, point in visible:
            bucketed[int(captured_at.timestamp() // bucket_seconds)] = point
        points = [bucketed[key] for key in sorted(bucketed)]
        for index, point in enumerate(points):
            point["delta"] = (
                point["total_points"] - points[index - 1]["total_points"]
                if index
                else 0
            )
        return {
            "range": selected_range,
            "points": points,
            "history_started_at": (
                parsed[0][1]["captured_at"] if parsed else ""
            ),
            "total_delta": (
                points[-1]["total_points"] - points[0]["total_points"]
                if len(points) > 1
                else 0
            ),
        }

    def _dashboard_view(
        self,
        redemptions: list[Any],
        group_id: str = "",
        history_range: str = "7d",
    ) -> dict[str, Any]:
        users = self.plugin.data.get("users", {})
        groups = self.plugin.data.get("groups", {})
        if not isinstance(users, dict):
            users = {}
        if not isinstance(groups, dict):
            groups = {}

        group_options = self._group_option_views(users, groups)
        selected_group_id = self._text(group_id, 120)
        selected_group = groups.get(selected_group_id) if selected_group_id else None
        if not isinstance(selected_group, dict):
            selected_group_id = ""
            selected_group = None
        if selected_group is not None:
            members = selected_group.get("members", {})
            member_ids = (
                {str(user_id) for user_id in members}
                if isinstance(members, dict)
                else set()
            )
            users = {
                user_id: record
                for user_id, record in users.items()
                if str(user_id) in member_ids
            }
            groups = {selected_group_id: selected_group}
            redemptions = [
                item
                for item in redemptions
                if isinstance(item, dict)
                and (
                    self._text(item.get("group_id"), 120) == selected_group_id
                    or (
                        not self._text(item.get("group_id"), 120)
                        and self._text(item.get("user_id"), 120) in member_ids
                    )
                )
            ]

        normalized_users = {
            str(user_id): record
            for user_id, record in users.items()
            if isinstance(record, dict)
        }
        balances = [
            self._int(record.get("points"), 0, -1_000_000_000, 1_000_000_000)
            for record in normalized_users.values()
        ]
        positive_total = sum(value for value in balances if value > 0)
        debt_total = abs(sum(value for value in balances if value < 0))
        total_points = sum(balances)
        today = datetime.date.today()
        today_text = today.isoformat()

        display_names: dict[str, str] = {}
        display_updates: dict[str, str] = {}
        group_views: list[dict[str, Any]] = []
        for raw_group_id, raw_group in groups.items():
            if not isinstance(raw_group, dict):
                continue
            members = raw_group.get("members", {})
            if not isinstance(members, dict):
                members = {}
            member_ids: list[str] = []
            for raw_user_id, member in members.items():
                user_id = str(raw_user_id)
                member_ids.append(user_id)
                if not isinstance(member, dict):
                    continue
                name = self._text(member.get("display_name"), 80)
                updated_at = self._text(member.get("updated_at"), 40)
                if name and updated_at >= display_updates.get(user_id, ""):
                    display_names[user_id] = name
                    display_updates[user_id] = updated_at
            member_balances = [
                self._int(normalized_users[user_id].get("points"), 0, -1_000_000_000, 1_000_000_000)
                for user_id in set(member_ids)
                if user_id in normalized_users
            ]
            group_views.append(
                {
                    "group_id": str(raw_group_id),
                    "member_count": len(set(member_ids)),
                    "tracked_count": len(member_balances),
                    "total_points": sum(member_balances),
                    "average_points": round(sum(member_balances) / len(member_balances), 1)
                    if member_balances
                    else 0,
                }
            )

        leaderboard = []
        for user_id, record in sorted(
            normalized_users.items(),
            key=lambda item: self._int(item[1].get("points"), 0, -1_000_000_000, 1_000_000_000),
            reverse=True,
        )[:10]:
            leaderboard.append(
                {
                    "user_id": user_id,
                    "display_name": display_names.get(user_id, user_id),
                    "points": self._int(record.get("points"), 0, -1_000_000_000, 1_000_000_000),
                    "streak": self._int(record.get("streak"), 0, 0, 100000),
                    "last_sign_in": self._date_text(record.get("last_sign_in")),
                }
            )

        distribution = [
            {"label": "负积分", "count": sum(1 for value in balances if value < 0), "tone": "rose"},
            {"label": "零积分", "count": sum(1 for value in balances if value == 0), "tone": "muted"},
            {"label": "1–99", "count": sum(1 for value in balances if 1 <= value < 100), "tone": "amber"},
            {"label": "100–499", "count": sum(1 for value in balances if 100 <= value < 500), "tone": "indigo"},
            {"label": "500 以上", "count": sum(1 for value in balances if value >= 500), "tone": "green"},
        ]

        daily: list[dict[str, Any]] = []
        for offset in range(6, -1, -1):
            day = today - datetime.timedelta(days=offset)
            day_text = day.isoformat()
            sign_ins = sum(
                1
                for record in normalized_users.values()
                if self._date_text(record.get("last_sign_in")) == day_text
            )
            redeemed = [
                item
                for item in redemptions
                if isinstance(item, dict)
                and self._date_text(item.get("redeemed_at")) == day_text
            ]
            daily.append(
                {
                    "date": day_text,
                    "sign_ins": sign_ins,
                    "redemptions": len(redeemed),
                    "points_spent": sum(
                        self._int(item.get("cost"), 0, 0, 1_000_000_000)
                        for item in redeemed
                    ),
                }
            )

        today_sign_ins = sum(
            1
            for record in normalized_users.values()
            if self._date_text(record.get("last_sign_in")) == today_text
        )
        today_activity_users = sum(
            1
            for record in normalized_users.values()
            if self._date_text(record.get("last_active_reward_date")) == today_text
        )
        today_lottery_draws = sum(
            self._int(record.get("daily_personal_lottery_times"), 0, 0, 100000)
            if self._date_text(record.get("last_personal_lottery_date")) == today_text
            else 0
            for record in normalized_users.values()
        )
        return {
            "summary": {
                "total_points": total_points,
                "positive_points": positive_total,
                "debt_points": debt_total,
                "user_count": len(normalized_users),
                "average_points": round(total_points / len(normalized_users), 1)
                if normalized_users
                else 0,
                "active_balance_users": sum(1 for value in balances if value != 0),
                "group_count": len(group_views),
            },
            "today": {
                "sign_ins": today_sign_ins,
                "activity_users": today_activity_users,
                "lottery_draws": today_lottery_draws,
                "redemptions": daily[-1]["redemptions"] if daily else 0,
            },
            "economy": {
                "total_sign_in_days": sum(
                    self._int(record.get("total_sign_in_days"), 0, 0, 10_000_000)
                    for record in normalized_users.values()
                ),
                "activity_points": sum(
                    self._int(record.get("activity_points"), 0, 0, 1_000_000_000)
                    for record in normalized_users.values()
                ),
                "lottery_points_won": sum(
                    self._int(record.get("lottery_points_won"), 0, 0, 1_000_000_000)
                    for record in normalized_users.values()
                ),
                "lottery_points_spent": sum(
                    self._int(record.get("lottery_points_spent"), 0, 0, 1_000_000_000)
                    for record in normalized_users.values()
                ),
                "exchange_points_spent": sum(
                    self._int(item.get("cost"), 0, 0, 1_000_000_000)
                    for item in redemptions
                    if isinstance(item, dict)
                ),
            },
            "scope": {
                "group_id": selected_group_id,
                "label": (
                    f"群 {selected_group_id}"
                    if selected_group_id
                    else "全部群聊"
                ),
            },
            "group_options": group_options,
            "point_history": self._point_history_view(
                selected_group_id, history_range
            ),
            "distribution": distribution,
            "daily": daily,
            "leaderboard": leaderboard,
            "groups": sorted(
                group_views,
                key=lambda item: item["total_points"],
                reverse=True,
            ),
        }

    def _overview_locked(self) -> dict[str, Any]:
        redemptions = self.plugin.data.get("exchange_redemptions", [])
        if not isinstance(redemptions, list):
            redemptions = []
        redeemed_hashes = {
            record.get("content_hash")
            for record in redemptions
            if isinstance(record, dict)
            and self._redemption_consumes_stock(record)
        }

        item_views: list[dict[str, Any]] = []
        total_stock = 0
        repeatable_count = 0
        enabled_count = 0
        for item in self.plugin._get_exchange_items():
            if self._item_repeatable(item):
                used_count = sum(
                    1
                    for record in redemptions
                    if isinstance(record, dict)
                    and self._text(record.get("item_name"), 120).casefold()
                    == self._text(item.get("name"), 120).casefold()
                )
            else:
                used_count = sum(
                    1
                    for content in item["contents"]
                    if self.plugin._exchange_content_fingerprint(content)
                    in redeemed_hashes
                )
            stock = (
                None
                if self._item_repeatable(item)
                else max(len(item["contents"]) - used_count, 0)
            )
            if stock is not None:
                total_stock += stock
            else:
                repeatable_count += 1
            enabled_count += int(item["enabled"])
            item_views.append(
                {
                    "name": item["name"],
                    "enabled": item["enabled"],
                    "cost": item["cost"],
                    "contents": item["contents"],
                    "content_type": self._content_type(
                        item.get("content_type"), item["contents"]
                    ),
                    "selection_mode": self._selection_mode(item.get("selection_mode")),
                    "repeatable": self._item_repeatable(item),
                    "private_only": item["private_only"],
                    "success_template": item["success_template"],
                    "stock": stock,
                    "used_count": used_count,
                    "total_count": len(item["contents"]),
                }
            )

        redemption_views = [
            view
            for view in (self._redemption_view(item) for item in reversed(redemptions))
            if view is not None
        ][:500]
        return {
            "points_name": self.plugin._get_points_name(),
            "dashboard": self._dashboard_view(redemptions),
            "settings": self._settings_view(),
            "exchange_scope": self._scope_view(),
            "items": item_views,
            "redemptions": redemption_views,
            "metrics": {
                "item_count": len(item_views),
                "enabled_count": enabled_count,
                "stock": total_stock,
                "repeatable_count": repeatable_count,
                "redeemed_count": len(redemptions),
                "points_spent": sum(
                    self._int(item.get("cost"), 0, 0, 1_000_000_000)
                    for item in redemptions
                    if isinstance(item, dict)
                ),
            },
            "revision": self._config_revision(),
            "can_save": callable(getattr(self.plugin.config, "save_config", None)),
        }

    async def overview(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                data = self._overview_locked()
            return {"status": "ok", "data": data}
        except Exception as exc:
            logger.warning("[PointSystem] 读取兑换概览失败: %s", exc)
            return {
                "status": "error",
                "message": "读取兑换配置失败，请检查配置后重试",
                "data": {},
            }

    async def dashboard(self) -> dict[str, Any]:
        try:
            group_id = self._text(self._request_query_value("group_id"), 120)
            history_range = self._text(self._request_query_value("range"), 8)
            async with self.plugin._data_lock:
                redemptions = self.plugin.data.get("exchange_redemptions", [])
                if not isinstance(redemptions, list):
                    redemptions = []
                data = self._dashboard_view(
                    redemptions,
                    group_id=group_id,
                    history_range=history_range,
                )
            return {"status": "ok", "data": {"dashboard": data}}
        except Exception as exc:
            logger.warning("[PointSystem] 读取积分总览失败: %s", exc)
            return {
                "status": "error",
                "message": "读取积分总览失败，请稍后重试",
                "data": {},
            }

    async def upload_media(self) -> dict[str, Any]:
        try:
            files = await request.files()
            uploaded = files.get("file") if files is not None else None
            if uploaded is None:
                return {"status": "error", "message": "请选择要上传的图片或视频", "data": {}}

            filename = self._text(getattr(uploaded, "filename", ""), 160)
            suffix = Path(filename).suffix.casefold()
            content_type = self._text(getattr(uploaded, "content_type", ""), 80).casefold()
            kind = "image" if content_type.startswith("image/") else "video" if content_type.startswith("video/") else ""
            if not kind:
                kind = next(
                    (candidate for candidate, extensions in MEDIA_EXTENSIONS.items() if suffix in extensions),
                    "",
                )
            if not kind or suffix not in MEDIA_EXTENSIONS[kind]:
                return {
                    "status": "error",
                    "message": "仅支持 PNG、JPG、GIF、WEBP 图片或 MP4、WEBM、MOV 视频",
                    "data": {},
                }

            chunks: list[bytes] = []
            total_size = 0
            while True:
                chunk = await uploaded.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MEDIA_UPLOAD_MAX_BYTES:
                    return {
                        "status": "error",
                        "message": "媒体文件不能超过 50 MB",
                        "data": {},
                    }
                chunks.append(chunk)
            if not chunks:
                return {"status": "error", "message": "不能上传空文件", "data": {}}

            media_dir = Path(getattr(self.plugin, "data_dir", ".")) / "exchange_media"
            media_dir.mkdir(parents=True, exist_ok=True)
            target = media_dir / f"{uuid.uuid4().hex}{suffix}"
            target.write_bytes(b"".join(chunks))
            return {
                "status": "ok",
                "data": {
                    "kind": kind,
                    "content": f"{kind}:{target}",
                    "filename": filename,
                },
            }
        except Exception as exc:
            logger.warning("[PointSystem] 上传兑换媒体失败: %s", exc)
            return {
                "status": "error",
                "message": "上传媒体失败，请稍后重试",
                "data": {},
            }

    def _validate_items(
        self, raw_items: Any
    ) -> tuple[list[dict[str, Any]], str]:
        if not isinstance(raw_items, list):
            return [], "兑换物数据格式不正确"
        if len(raw_items) > 100:
            return [], "兑换物最多配置 100 个"

        items: list[dict[str, Any]] = []
        names: set[str] = set()
        contents_seen: set[str] = set()
        for index, raw_item in enumerate(raw_items, start=1):
            if not isinstance(raw_item, dict):
                return [], f"第 {index} 个兑换物格式不正确"
            name = self._text(raw_item.get("name"), 120)
            if not name:
                return [], f"第 {index} 个兑换物缺少名称"
            normalized_name = name.casefold()
            if normalized_name in names:
                return [], f"兑换物名称“{name}”重复"
            names.add(normalized_name)

            raw_contents = raw_item.get("contents", [])
            if not isinstance(raw_contents, list):
                return [], f"“{name}”的发放内容必须是列表"
            if len(raw_contents) > 10000:
                return [], f"“{name}”的发放内容最多 10000 条"
            contents: list[str] = []
            local_contents: set[str] = set()
            for raw_content in raw_contents:
                content = self._text(raw_content, 4000)
                if not content or content in local_contents:
                    continue
                if content in contents_seen:
                    return [], f"发放内容在多个兑换物中重复：{content[:40]}"
                local_contents.add(content)
                contents_seen.add(content)
                contents.append(content)

            raw_content_type = self._text(raw_item.get("content_type"), 20).casefold()
            content_type = self._content_type(raw_content_type, contents)
            if raw_content_type and raw_content_type not in {"text", "image", "video"}:
                return [], f"“{name}”的奖励类型不受支持"
            if raw_content_type:
                media_types = {self._media_kind(content) for content in contents}
                media_types.discard("")
                if content_type == "text" and media_types:
                    return [], f"“{name}”已选择文本类型，请移除图片/视频内容"
                if content_type in {"image", "video"} and media_types != {content_type}:
                    return [], f"“{name}”已选择{content_type}类型，内容必须全部是对应媒体"

            template = self._text(raw_item.get("success_template"), 4000)
            if not template:
                template = (
                    "兑换成功！\n兑换物：{item}\n兑换内容：{content}\n"
                    "消耗 {cost} {points_name}，剩余 {remaining} {points_name}。"
                )
            items.append(
                {
                    "__template_key": "default",
                    "name": name,
                    "enabled": self._bool(raw_item.get("enabled"), True),
                    "cost": self._int(raw_item.get("cost"), 100, 1, 1_000_000_000),
                    "contents": contents,
                    "content_type": content_type,
                    "selection_mode": self._selection_mode(raw_item.get("selection_mode")),
                    "repeatable": self._bool(raw_item.get("repeatable"), False),
                    "private_only": self._bool(raw_item.get("private_only"), True),
                    "success_template": template,
                }
            )
        return items, ""

    async def save_items(self) -> dict[str, Any]:
        try:
            payload = await self._payload()
            async with self._settings_lock:
                base_revision = self._text(payload.get("revision"), 64)
                current_revision = self._config_revision()
                if base_revision and base_revision != current_revision:
                    return {
                        "status": "error",
                        "message": "兑换配置已在其他页面更新，请刷新后再保存",
                        "data": {"revision": current_revision},
                    }

                items, error = self._validate_items(payload.get("items"))
                if error:
                    return {"status": "error", "message": error}
                scope, scope_error = self._validate_scope(
                    payload.get(
                        "exchange_scope",
                        self.plugin.config.get("exchange_scope", {}),
                    )
                )
                if scope_error:
                    return {"status": "error", "message": scope_error}

                config_snapshot = copy.deepcopy(dict(self.plugin.config))
                async with self.plugin._data_lock:
                    self.plugin.config["exchange_items"] = items
                    self.plugin.config["exchange_scope"] = scope
                    saver = getattr(self.plugin.config, "save_config", None)
                    if callable(saver):
                        try:
                            result = saver()
                            if hasattr(result, "__await__"):
                                await result
                        except Exception as exc:
                            self.plugin.config.clear()
                            self.plugin.config.update(config_snapshot)
                            logger.warning("[PointSystem] 保存兑换配置失败: %s", exc)
                            return {
                                "status": "error",
                                "message": "AstrBot 配置保存失败，本次修改未生效",
                            }
                    data = self._overview_locked()
                return {"status": "ok", "data": data}
        except Exception as exc:
            logger.warning("[PointSystem] 页面保存兑换配置失败: %s", exc)
            return {
                "status": "error",
                "message": "保存兑换配置失败，请刷新后重试",
                "data": {},
            }

    async def save_settings(self) -> dict[str, Any]:
        try:
            payload = await self._payload()
            async with self._settings_lock:
                base_revision = self._text(payload.get("revision"), 64)
                current_revision = self._config_revision()
                if base_revision and base_revision != current_revision:
                    return {
                        "status": "error",
                        "message": "插件配置已在其他页面更新，请刷新后再保存",
                        "data": {"revision": current_revision},
                    }

                settings, error = self._validate_settings(payload.get("settings"))
                if error:
                    return {"status": "error", "message": error, "data": {}}

                config_snapshot = copy.deepcopy(dict(self.plugin.config))
                async with self.plugin._data_lock:
                    for key, value in settings.items():
                        if isinstance(value, dict):
                            current = self.plugin.config.get(key, {})
                            merged = copy.deepcopy(current) if isinstance(current, dict) else {}
                            merged.update(value)
                            self.plugin.config[key] = merged
                        else:
                            self.plugin.config[key] = value

                    saver = getattr(self.plugin.config, "save_config", None)
                    if callable(saver):
                        try:
                            result = saver()
                            if hasattr(result, "__await__"):
                                await result
                        except Exception as exc:
                            self.plugin.config.clear()
                            self.plugin.config.update(config_snapshot)
                            logger.warning("[PointSystem] 保存常用配置失败: %s", exc)
                            return {
                                "status": "error",
                                "message": "AstrBot 配置保存失败，本次修改未生效",
                                "data": {},
                            }
                    data = self._overview_locked()
                return {"status": "ok", "data": data}
        except Exception as exc:
            logger.warning("[PointSystem] 页面保存常用配置失败: %s", exc)
            return {
                "status": "error",
                "message": "保存常用配置失败，请刷新后重试",
                "data": {},
            }
