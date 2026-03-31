import asyncio
import datetime
import json
import os
import random
import re
import shutil
from typing import Any, Dict

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Plain, Reply
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

PLUGIN_NAME = "astrbot_plugin_point_system"
DATA_VERSION = 7
DEFAULT_POINTS_NAME = "积分"
GLOBAL_SIGN_IN_SCOPE_ID = "__global_sign_in__"
BIRTHDAY_SIGN_IN_TRIGGER = "生日签到"
BIRTHDAY_SIGN_IN_REWARD = 50
INVALID_DISPLAY_NAMES = {
    "未绑定账号",
    "未知用户",
    "unknown",
    "Unknown",
    "用户",
}
LEGACY_DEFAULT_TEMPLATES = {
    "sign_in_success": "签到成功！获得 {points} {name}。您当前共有 {total} {name}。",
    "already_signed_in": "您今天已经签到过了，明天再来吧~",
    "query_points": "报告！您当前拥有 {total} {name}。",
}
DEFAULT_TEMPLATES = {
    "sign_in_success": (
        "{user}签到成功，获得 {points} {name}{bonus_detail}。"
        "当前共有 {total} {name}，连续签到 {streak} 天，累计签到 {total_sign_in_days} 天。"
    ),
    "already_signed_in": (
        "{user}今天已经签到过啦。"
        "当前共有 {total} {name}，连续签到 {streak} 天，累计签到 {total_sign_in_days} 天。"
    ),
    "query_points": (
        "{user}当前拥有 {total} {name}。"
        "连续签到 {streak} 天，累计签到 {total_sign_in_days} 天，今日状态：{sign_in_status}。"
    ),
}
DEFAULT_PERSONAL_LOTTERY_PRIZES = {
    "fifth": {
        "label": "五等奖",
        "min_points": 0,
        "max_points": 5,
        "weight": 40.0,
    },
    "fourth": {
        "label": "四等奖",
        "min_points": 6,
        "max_points": 15,
        "weight": 30.0,
    },
    "third": {
        "label": "三等奖",
        "min_points": 16,
        "max_points": 25,
        "weight": 20.0,
    },
    "second": {
        "label": "二等奖",
        "min_points": 26,
        "max_points": 40,
        "weight": 9.7,
    },
    "first": {
        "label": "一等奖",
        "min_points": 100,
        "max_points": 100,
        "weight": 0.3,
    },
}
DEFAULT_GROUP_LOTTERY_RATIOS = [1.0, 9.0, 20.0, 25.0, 35.0]
COMMAND_TEXT_PATTERN = re.compile(
    r"^(?:签到|生日签到|记录生日|我的积分|积分榜|积分规则|抽奖|兑换头衔|兑换设精|兑换禁言|给积分|扣积分)(?:\s|$)"
)


@register(
    PLUGIN_NAME,
    "AstrBot",
    "群积分助手。支持每日签到、活跃奖励、按群排行榜，以及个人抽奖、群体抽奖、日期口令奖励和头衔/设精/禁言等互动功能。",
    "1.8.0",
    "https://github.com/astrbot/astrbot_plugin_point_system",
)
class PointSystemPlugin(Star):
    def __init__(self, context: Context, config: Dict[str, Any]):
        super().__init__(context)
        self.config = config
        self._data_lock = asyncio.Lock()
        self._backup_task: asyncio.Task | None = None
        self._backup_stop_event = asyncio.Event()
        self._birthday_broadcast_task: asyncio.Task | None = None
        self._birthday_broadcast_stop_event = asyncio.Event()

        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.data_file = os.path.join(self.data_dir, "points_data.json")
        os.makedirs(self.data_dir, exist_ok=True)

        self.data, migrated = self._load_data_sync()
        if migrated:
            self._write_data_sync()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self._backup_task = loop.create_task(self._auto_backup_loop())
            self._birthday_broadcast_task = loop.create_task(
                self._birthday_broadcast_loop()
            )

    def _new_store(self) -> Dict[str, Any]:
        return {
            "version": DATA_VERSION,
            "users": {},
            "groups": {},
        }

    def _normalize_int(self, value: Any, default: int, minimum: int = 0) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = default
        return max(minimum, result)

    def _normalize_float(
        self, value: Any, default: float, minimum: float = 0.0
    ) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            result = default
        return max(minimum, result)

    def _normalize_signed_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _normalize_text(self, value: Any) -> str:
        return value if isinstance(value, str) else ""

    def _single_line_message(self, text: Any) -> str:
        if text is None:
            return ""
        return str(text).replace("\r", "").replace("\n", "").strip()

    def _normalize_backup_time(self, value: Any) -> str:
        text = self._normalize_text(value).strip()
        if re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", text):
            hour_text, minute_text = text.split(":")
            return f"{int(hour_text):02d}:{int(minute_text):02d}"
        return "03:00"

    def _normalize_birthday_md(self, value: Any) -> str:
        text = self._normalize_text(value).strip()
        if not text:
            return ""
        match = re.fullmatch(r"(\d{1,2})\s*[/\-\.]\s*(\d{1,2})", text)
        if not match:
            return ""
        month = int(match.group(1))
        day = int(match.group(2))
        try:
            datetime.date(2000, month, day)
        except ValueError:
            return ""
        return f"{month:02d}/{day:02d}"

    def _normalize_string_list(self, value: Any) -> list[str]:
        if isinstance(value, str):
            raw_items = re.split(r"[,，\n]", value)
        elif isinstance(value, list):
            raw_items = [str(item) for item in value]
        else:
            raw_items = []

        result: list[str] = []
        for item in raw_items:
            text = " ".join(str(item).strip().split())
            if text:
                result.append(text)
        return result

    def _normalize_backup_paths(self, value: Any) -> list[str]:
        paths = self._normalize_string_list(value)
        normalized_paths: list[str] = []
        for path in paths:
            expanded = os.path.expandvars(os.path.expanduser(path))
            if expanded:
                normalized_paths.append(expanded)
        return normalized_paths

    def _normalize_counter_map(self, raw: Any) -> Dict[str, Dict[str, Any]]:
        if not isinstance(raw, dict):
            return {}

        normalized: Dict[str, Dict[str, Any]] = {}
        for raw_key, raw_value in raw.items():
            key = str(raw_key).strip()
            if not key:
                continue
            value = raw_value if isinstance(raw_value, dict) else {}
            normalized[key] = {
                "date": self._normalize_text(value.get("date")),
                "count": self._normalize_int(value.get("count"), 0, 0),
            }
        return normalized

    def _normalize_group_lottery_pool(self, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raw = {}

        raw_participants = raw.get("participants", [])
        if not isinstance(raw_participants, list):
            raw_participants = []

        participants: list[Dict[str, Any]] = []
        for item in raw_participants:
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("user_id", "")).strip()
            if not user_id:
                continue
            participants.append(
                {
                    "user_id": user_id,
                    "display_name": self._safe_display_name(
                        item.get("display_name"), user_id
                    ),
                    "paid_points": self._normalize_int(
                        item.get("paid_points"), 0, minimum=0
                    ),
                    "joined_at": self._normalize_text(item.get("joined_at")),
                }
            )

        return {
            "date": self._normalize_text(raw.get("date")),
            "participants": participants,
        }

    def _normalize_display_name(self, name: Any) -> str | None:
        if not isinstance(name, str):
            return None

        cleaned = " ".join(name.strip().split())
        if not cleaned or cleaned in INVALID_DISPLAY_NAMES:
            return None
        return cleaned

    def _normalize_user_record(self, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raw = {}
        return {
            "points": self._normalize_signed_int(raw.get("points"), 0),
            "last_sign_in": self._normalize_text(raw.get("last_sign_in")),
            "streak": self._normalize_int(raw.get("streak"), 0, 0),
            "total_sign_in_days": self._normalize_int(
                raw.get("total_sign_in_days"), 0, 0
            ),
            "first_sign_in_at": self._normalize_text(raw.get("first_sign_in_at")),
            "last_active_reward_at": self._normalize_text(
                raw.get("last_active_reward_at")
            ),
            "last_active_reward_date": self._normalize_text(
                raw.get("last_active_reward_date")
            ),
            "daily_active_point_times": self._normalize_int(
                raw.get("daily_active_point_times"), 0, 0
            ),
            "activity_points": self._normalize_int(raw.get("activity_points"), 0, 0),
            "last_personal_lottery_date": self._normalize_text(
                raw.get("last_personal_lottery_date", raw.get("last_lottery_date"))
            ),
            "daily_personal_lottery_times": self._normalize_int(
                raw.get(
                    "daily_personal_lottery_times", raw.get("daily_lottery_times")
                ),
                0,
                0,
            ),
            "last_group_lottery_join_date": self._normalize_text(
                raw.get("last_group_lottery_join_date")
            ),
            "daily_group_lottery_join_times": self._normalize_int(
                raw.get("daily_group_lottery_join_times"), 0, 0
            ),
            "lottery_draw_count": self._normalize_int(
                raw.get("lottery_draw_count"), 0, 0
            ),
            "lottery_points_spent": self._normalize_int(
                raw.get("lottery_points_spent"), 0, 0
            ),
            "lottery_points_won": self._normalize_int(
                raw.get("lottery_points_won"), 0, 0
            ),
            "fortune_lucky_pity_count": self._normalize_int(
                raw.get("fortune_lucky_pity_count"), 0, 0
            ),
            "fortune_unlucky_pity_count": self._normalize_int(
                raw.get("fortune_unlucky_pity_count"), 0, 0
            ),
            "birthday_md": self._normalize_text(raw.get("birthday_md")),
            "last_birthday_sign_in_year": self._normalize_text(
                raw.get("last_birthday_sign_in_year")
            ),
            "special_reward_claims": self._normalize_counter_map(
                raw.get("special_reward_claims")
            ),
        }

    def _mask_user_id(self, user_id: str) -> str:
        if len(user_id) <= 4:
            return f"{user_id}***"
        return f"{user_id[:4]}***"

    def _safe_display_name(self, name: Any, user_id: str) -> str:
        normalized = self._normalize_display_name(name)
        if not normalized:
            return f"用户({self._mask_user_id(user_id)})"

        if len(normalized) > 24:
            return f"{normalized[:21]}..."
        return normalized

    def _safe_reply_name(self, name: Any) -> str:
        normalized = self._normalize_display_name(name)
        if not normalized:
            return "你"
        if len(normalized) > 12:
            return normalized[:12]
        return normalized

    def _normalize_group_store(
        self, raw_groups: Any, normalized_users: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not isinstance(raw_groups, dict):
            return {}

        groups: Dict[str, Any] = {}
        for raw_group_id, raw_group_info in raw_groups.items():
            group_id = str(raw_group_id).strip()
            if not group_id or not isinstance(raw_group_info, dict):
                continue

            raw_members = raw_group_info.get("members", {})
            if not isinstance(raw_members, dict):
                raw_members = {}

            members: Dict[str, Any] = {}
            for raw_user_id, raw_member_info in raw_members.items():
                user_id = str(raw_user_id).strip()
                if not user_id:
                    continue

                if user_id not in normalized_users:
                    normalized_users[user_id] = self._normalize_user_record({})

                member_info = raw_member_info if isinstance(raw_member_info, dict) else {}
                members[user_id] = {
                    "display_name": self._safe_display_name(
                        member_info.get("display_name"), user_id
                    ),
                    "updated_at": self._normalize_text(member_info.get("updated_at")),
                    "negative_title": self._normalize_text(
                        member_info.get("negative_title")
                    ),
                }

            groups[group_id] = {
                "members": members,
                "group_lottery_pool": self._normalize_group_lottery_pool(
                    raw_group_info.get("group_lottery_pool")
                ),
                "message_target": self._normalize_text(
                    raw_group_info.get("message_target")
                ),
                "daily_first_sign_in_date": self._normalize_text(
                    raw_group_info.get("daily_first_sign_in_date")
                ),
                "daily_first_sign_in_user_id": self._normalize_text(
                    raw_group_info.get("daily_first_sign_in_user_id")
                ),
                "last_birthday_broadcast_date": self._normalize_text(
                    raw_group_info.get("last_birthday_broadcast_date")
                ),
            }
        return groups

    def _normalize_store(self, raw: Any) -> tuple[Dict[str, Any], bool]:
        store = self._new_store()
        migrated = False

        if not isinstance(raw, dict):
            return store, True

        if "users" in raw or "groups" in raw:
            raw_users = raw.get("users", {})
            if not isinstance(raw_users, dict):
                raw_users = {}
                migrated = True

            normalized_users = {
                str(user_id): self._normalize_user_record(user_info)
                for user_id, user_info in raw_users.items()
                if str(user_id).strip()
            }
            groups = self._normalize_group_store(raw.get("groups", {}), normalized_users)

            if raw.get("version") != DATA_VERSION:
                migrated = True

            store["users"] = normalized_users
            store["groups"] = groups
            return store, migrated

        # 兼容旧版扁平结构：{user_id: user_record}
        legacy_users = {
            str(user_id): self._normalize_user_record(user_info)
            for user_id, user_info in raw.items()
            if isinstance(user_info, dict) and str(user_id).strip()
        }
        store["users"] = legacy_users
        return store, True

    def _load_data_sync(self) -> tuple[Dict[str, Any], bool]:
        if not os.path.exists(self.data_file):
            return self._new_store(), False

        try:
            with open(self.data_file, "r", encoding="utf-8") as file:
                raw_data = json.load(file)
        except Exception as exc:
            logger.error(f"加载积分数据失败: {exc}")
            return self._new_store(), True

        return self._normalize_store(raw_data)

    def _write_data_sync(self) -> None:
        temp_file = f"{self.data_file}.tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as file:
                json.dump(self.data, file, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(temp_file, self.data_file)
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass

    async def _save_data_locked(self) -> None:
        try:
            await asyncio.to_thread(self._write_data_sync)
        except Exception as exc:
            logger.error(f"保存积分数据失败: {exc}")

    def _build_backup_file_path(self, backup_path: str) -> str:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if os.path.isdir(backup_path) or not os.path.splitext(backup_path)[1]:
            os.makedirs(backup_path, exist_ok=True)
            return os.path.join(backup_path, f"points_data_{timestamp}.json")

        parent_dir = os.path.dirname(backup_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        root, ext = os.path.splitext(backup_path)
        ext = ext or ".json"
        return f"{root}_{timestamp}{ext}"

    def _perform_backup_sync(self, backup_paths: list[str]) -> int:
        success_count = 0
        for backup_path in backup_paths:
            target_file = self._build_backup_file_path(backup_path)
            shutil.copy2(self.data_file, target_file)
            success_count += 1
        return success_count

    async def _run_backup(self, reason: str) -> None:
        backup_cfg = self._get_backup_settings()
        if not backup_cfg["enabled"]:
            return

        async with self._data_lock:
            await self._save_data_locked()
            try:
                success_count = await asyncio.to_thread(
                    self._perform_backup_sync, backup_cfg["backup_paths"]
                )
            except Exception as exc:
                logger.error(f"{reason}失败: {exc}")
                return

        logger.info(
            f"{reason}完成，已写入 {success_count} 个备份目标。"
        )

    async def _auto_backup_loop(self) -> None:
        while not self._backup_stop_event.is_set():
            backup_cfg = self._get_backup_settings()
            if not backup_cfg["enabled"]:
                try:
                    await asyncio.wait_for(self._backup_stop_event.wait(), timeout=300)
                except asyncio.TimeoutError:
                    continue
                break

            time_text = backup_cfg["auto_backup_time"]
            hour, minute = [int(part) for part in time_text.split(":")]
            now = datetime.datetime.now()
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += datetime.timedelta(days=1)

            wait_seconds = max((next_run - now).total_seconds(), 1)
            try:
                await asyncio.wait_for(
                    self._backup_stop_event.wait(), timeout=wait_seconds
                )
                break
            except asyncio.TimeoutError:
                await self._run_backup(f"自动备份({time_text})")

    def _get_points_name(self) -> str:
        value = self.config.get("points_name", DEFAULT_POINTS_NAME)
        if not isinstance(value, str):
            return DEFAULT_POINTS_NAME
        value = value.strip()
        return value or DEFAULT_POINTS_NAME

    def _get_sign_in_settings(self) -> Dict[str, Any]:
        sign_in_cfg = self.config.get("sign_in_settings", {})
        if not isinstance(sign_in_cfg, dict):
            sign_in_cfg = {}

        raw_mode = str(sign_in_cfg.get("sign_in_mode", "random")).strip().lower()
        sign_in_mode = raw_mode if raw_mode in {"random", "fixed"} else "random"
        min_points = self._normalize_int(
            sign_in_cfg.get("min_sign_in_points"), 10, minimum=0
        )
        max_points = self._normalize_int(
            sign_in_cfg.get("max_sign_in_points"), 30, minimum=0
        )
        if max_points < min_points:
            min_points, max_points = max_points, min_points

        return {
            "sign_in_mode": sign_in_mode,
            "fixed_sign_in_points": self._normalize_int(
                sign_in_cfg.get("fixed_sign_in_points"), 20, minimum=0
            ),
            "min_sign_in_points": min_points,
            "max_sign_in_points": max_points,
            "first_sign_in_bonus": self._normalize_int(
                sign_in_cfg.get("first_sign_in_bonus"), 20, minimum=0
            ),
            "daily_first_sign_in_bonus": self._normalize_int(
                sign_in_cfg.get("daily_first_sign_in_bonus"), 0, minimum=0
            ),
            "fortune_event_enabled": bool(
                sign_in_cfg.get("fortune_event_enabled", True)
            ),
            "fortune_event_chance": min(
                self._normalize_float(
                    sign_in_cfg.get("fortune_event_chance"), 0.002, minimum=0.0
                ),
                1.0,
            ),
            "fortune_event_points": self._normalize_int(
                sign_in_cfg.get("fortune_event_points"), 300, minimum=0
            ),
            "fortune_pity_enabled": bool(
                sign_in_cfg.get("fortune_pity_enabled", True)
            ),
            "fortune_lucky_pity_threshold": self._normalize_int(
                sign_in_cfg.get("fortune_lucky_pity_threshold"), 100, minimum=0
            ),
            "fortune_unlucky_pity_threshold": self._normalize_int(
                sign_in_cfg.get("fortune_unlucky_pity_threshold"), 100, minimum=0
            ),
            "streak_bonus_enabled": bool(
                sign_in_cfg.get("streak_bonus_enabled", True)
            ),
            "streak_step_bonus": self._normalize_int(
                sign_in_cfg.get("streak_step_bonus"), 2, minimum=0
            ),
            "streak_bonus_cap": self._normalize_int(
                sign_in_cfg.get("streak_bonus_cap"), 20, minimum=0
            ),
            "weekly_streak_bonus": self._normalize_int(
                sign_in_cfg.get("weekly_streak_bonus"), 15, minimum=0
            ),
        }

    def _get_activity_settings(self) -> Dict[str, Any]:
        activity_cfg = self.config.get("activity_settings", {})
        if not isinstance(activity_cfg, dict):
            activity_cfg = {}

        return {
            "enabled": bool(activity_cfg.get("enabled", True)),
            "points_per_message": self._normalize_int(
                activity_cfg.get("points_per_message"), 1, minimum=0
            ),
            "cooldown_seconds": self._normalize_int(
                activity_cfg.get("cooldown_seconds"), 300, minimum=1
            ),
            "daily_limit": self._normalize_int(
                activity_cfg.get("daily_limit"), 6, minimum=1
            ),
            "min_text_length": self._normalize_int(
                activity_cfg.get("min_text_length"), 4, minimum=1
            ),
        }

    def _normalize_trigger_token(self, value: Any, default: str = "") -> str:
        if not isinstance(value, str):
            return default
        normalized = "".join(value.strip().split())
        return normalized or default

    def _extract_trigger_keyword(self, value: Any, action_word: str) -> str:
        normalized = self._normalize_trigger_token(value)
        if not normalized:
            return ""
        if normalized.startswith(action_word) and len(normalized) > len(action_word):
            return normalized[len(action_word) :]
        if normalized.endswith(action_word) and len(normalized) > len(action_word):
            return normalized[: -len(action_word)]
        return normalized

    def _get_sign_in_trigger_keyword(self) -> str:
        configured = self._normalize_trigger_token(
            self.config.get("sign_in_trigger_keyword"), ""
        )
        if configured:
            return configured

        legacy_trigger = self.config.get("sign_in_trigger", "星缘签到")
        extracted = self._extract_trigger_keyword(legacy_trigger, "签到")
        return extracted or "星缘"

    def _get_lottery_trigger_keyword(self) -> str:
        configured = self._normalize_trigger_token(
            self.config.get("lottery_trigger_keyword"), ""
        )
        if configured:
            return configured

        extracted = self._extract_trigger_keyword(
            self.config.get("lottery_trigger", ""), "抽奖"
        )
        if extracted:
            return extracted
        return self._get_sign_in_trigger_keyword()

    def _get_action_trigger_variants(self, action_word: str) -> list[str]:
        keyword = (
            self._get_sign_in_trigger_keyword()
            if action_word == "签到"
            else self._get_lottery_trigger_keyword()
        )
        variants: list[str] = []
        if keyword:
            variants.append(f"{keyword}{action_word}")
            variants.append(f"{action_word}{keyword}")

        if action_word == "签到":
            legacy_exact = self._normalize_trigger_token(
                self.config.get("sign_in_trigger", "")
            )
            if legacy_exact and legacy_exact not in variants:
                variants.append(legacy_exact)
        elif action_word == "抽奖":
            legacy_exact = self._normalize_trigger_token(
                self.config.get("lottery_trigger", "")
            )
            if legacy_exact and legacy_exact not in variants:
                variants.append(legacy_exact)
        return variants

    def _get_sign_in_triggers(self) -> list[str]:
        return self._get_action_trigger_variants("签到")

    def _get_lottery_triggers(self) -> list[str]:
        return self._get_action_trigger_variants("抽奖")

    def _match_quick_action(self, message: str) -> str | None:
        normalized = self._normalize_trigger_token(message)
        if not normalized:
            return None
        if normalized in self._get_sign_in_triggers():
            return "sign_in"
        if normalized in self._get_lottery_triggers():
            return "lottery"
        return None

    def _get_leaderboard_settings(self) -> tuple[int, bool]:
        leaderboard_cfg = self.config.get("leaderboard_settings", {})
        if not isinstance(leaderboard_cfg, dict):
            leaderboard_cfg = {}

        display_limit = self._normalize_int(
            leaderboard_cfg.get("display_limit"), 10, minimum=1
        )
        return min(display_limit, 50), bool(
            leaderboard_cfg.get("show_self_rank", True)
        )

    def _get_admin_settings(self) -> tuple[bool, int]:
        admin_cfg = self.config.get("admin_settings", {})
        if not isinstance(admin_cfg, dict):
            admin_cfg = {}

        log_operations = bool(admin_cfg.get("log_operations", True))
        max_admin_give = self._normalize_int(
            admin_cfg.get("max_admin_give"), 1000, minimum=1
        )
        return log_operations, max_admin_give

    def _get_points_admin_ids(self) -> set[str]:
        admin_cfg = self.config.get("admin_settings", {})
        if not isinstance(admin_cfg, dict):
            admin_cfg = {}

        raw_ids = admin_cfg.get("points_admin_ids", [])
        if isinstance(raw_ids, str):
            raw_values = [item.strip() for item in raw_ids.split(",")]
        elif isinstance(raw_ids, list):
            raw_values = [str(item).strip() for item in raw_ids]
        else:
            raw_values = []

        return {item for item in raw_values if item.isdigit()}

    def _get_exchange_settings(self) -> Dict[str, Any]:
        exchange_cfg = self.config.get("exchange_settings", {})
        if not isinstance(exchange_cfg, dict):
            exchange_cfg = {}

        return {
            "title_enabled": bool(exchange_cfg.get("title_enabled", True)),
            "title_cost": self._normalize_int(
                exchange_cfg.get("title_cost"), 200, minimum=1
            ),
            "title_max_length": min(
                self._normalize_int(exchange_cfg.get("title_max_length"), 6, minimum=1),
                16,
            ),
            "essence_enabled": bool(exchange_cfg.get("essence_enabled", True)),
            "essence_cost": self._normalize_int(
                exchange_cfg.get("essence_cost"), 300, minimum=1
            ),
            "mute_enabled": bool(exchange_cfg.get("mute_enabled", True)),
            "mute_cost": self._normalize_int(
                exchange_cfg.get("mute_cost"), 500, minimum=1
            ),
            "mute_duration_seconds": min(
                self._normalize_int(
                    exchange_cfg.get("mute_duration_seconds"), 60, minimum=1
                ),
                2592000,
            ),
            "allow_mute_others": bool(
                exchange_cfg.get("allow_mute_others", False)
            ),
        }

    def _get_backup_settings(self) -> Dict[str, Any]:
        backup_cfg = self.config.get("backup_settings", {})
        if not isinstance(backup_cfg, dict):
            backup_cfg = {}

        paths = self._normalize_backup_paths(backup_cfg.get("backup_paths", []))
        return {
            "enabled": bool(backup_cfg.get("enabled", False)) and bool(paths),
            "backup_paths": paths,
            "auto_backup_time": self._normalize_backup_time(
                backup_cfg.get("auto_backup_time", "03:00")
            ),
        }

    def _normalize_personal_lottery_prizes(self, raw_prizes: Any) -> list[Dict[str, Any]]:
        if not isinstance(raw_prizes, dict):
            raw_prizes = {}

        prizes: list[Dict[str, Any]] = []
        total_weight = 0.0
        for key, defaults in DEFAULT_PERSONAL_LOTTERY_PRIZES.items():
            prize_cfg = raw_prizes.get(key, {})
            if not isinstance(prize_cfg, dict):
                prize_cfg = {}

            label = prize_cfg.get("label", defaults["label"])
            if not isinstance(label, str) or not label.strip():
                label = defaults["label"]

            min_points = self._normalize_int(
                prize_cfg.get("min_points"), defaults["min_points"], minimum=0
            )
            max_points = self._normalize_int(
                prize_cfg.get("max_points"), defaults["max_points"], minimum=0
            )
            if max_points < min_points:
                min_points, max_points = max_points, min_points

            weight = self._normalize_float(
                prize_cfg.get("weight"), defaults["weight"], minimum=0.0
            )
            total_weight += weight
            prizes.append(
                {
                    "key": key,
                    "label": label.strip(),
                    "min_points": min_points,
                    "max_points": max_points,
                    "weight": weight,
                }
            )

        if total_weight <= 0:
            return [
                {
                    "key": key,
                    "label": defaults["label"],
                    "min_points": defaults["min_points"],
                    "max_points": defaults["max_points"],
                    "weight": defaults["weight"],
                }
                for key, defaults in DEFAULT_PERSONAL_LOTTERY_PRIZES.items()
            ]

        return prizes

    def _normalize_ratio_values(
        self, raw_values: Any, default: list[float]
    ) -> list[float]:
        if isinstance(raw_values, str):
            values = re.split(r"[,，\s]+", raw_values.strip())
        elif isinstance(raw_values, list):
            values = raw_values
        else:
            values = []

        ratios = [
            self._normalize_float(item, 0.0, minimum=0.0)
            for item in values
            if str(item).strip()
        ]
        ratios = [item for item in ratios if item > 0]
        return ratios or default.copy()

    def _get_lottery_settings(self) -> Dict[str, Any]:
        lottery_cfg = self.config.get("lottery_settings", {})
        if not isinstance(lottery_cfg, dict):
            lottery_cfg = {}

        raw_default_mode = str(
            lottery_cfg.get("default_mode", lottery_cfg.get("mode", "personal"))
        ).strip().lower()
        mode_alias_map = {
            "single": "personal",
            "personal": "personal",
            "个人": "personal",
            "单人": "personal",
            "shared": "group",
            "group": "group",
            "群体": "group",
            "团体": "group",
        }
        default_mode = mode_alias_map.get(raw_default_mode, "personal")

        personal_enabled = bool(lottery_cfg.get("personal_enabled", True))
        group_enabled = bool(lottery_cfg.get("group_enabled", True))
        enabled = bool(lottery_cfg.get("enabled", True)) and (
            personal_enabled or group_enabled
        )

        if default_mode == "personal" and not personal_enabled and group_enabled:
            default_mode = "group"
        elif default_mode == "group" and not group_enabled and personal_enabled:
            default_mode = "personal"

        personal_prizes = self._normalize_personal_lottery_prizes(
            lottery_cfg.get("personal_prizes", lottery_cfg.get("prizes", {}))
        )
        group_ratios = self._normalize_ratio_values(
            lottery_cfg.get("group_distribution_ratios"),
            DEFAULT_GROUP_LOTTERY_RATIOS,
        )
        group_required_participants = self._normalize_int(
            lottery_cfg.get("group_required_participants"), 5, minimum=2
        )
        if len(group_ratios) < group_required_participants:
            group_ratios.extend([1.0] * (group_required_participants - len(group_ratios)))
        elif len(group_ratios) > group_required_participants:
            group_ratios = group_ratios[:group_required_participants]

        return {
            "enabled": enabled,
            "default_mode": default_mode,
            "personal_enabled": personal_enabled,
            "group_enabled": group_enabled,
            "personal_cost": self._normalize_int(
                lottery_cfg.get("personal_cost", lottery_cfg.get("cost", 20)),
                20,
                minimum=0,
            ),
            "personal_daily_limit": self._normalize_int(
                lottery_cfg.get(
                    "personal_daily_limit", lottery_cfg.get("single_daily_limit", 1)
                ),
                1,
                minimum=1,
            ),
            "personal_prizes": personal_prizes,
            "group_cost": self._normalize_int(
                lottery_cfg.get("group_cost", lottery_cfg.get("cost", 20)),
                20,
                minimum=0,
            ),
            "group_daily_limit_per_user": self._normalize_int(
                lottery_cfg.get("group_daily_limit_per_user"), 1, minimum=1
            ),
            "group_required_participants": group_required_participants,
            "group_distribution_ratios": group_ratios,
        }

    def _get_special_date_reward_entries(self) -> list[Dict[str, Any]]:
        raw_entries = self.config.get("special_date_reward_entries", [])
        if not isinstance(raw_entries, list):
            raw_entries = []

        entries: list[Dict[str, Any]] = []
        for index, raw_item in enumerate(raw_entries, start=1):
            if not isinstance(raw_item, dict):
                continue

            name = raw_item.get("name")
            if not isinstance(name, str) or not name.strip():
                name = f"日期奖励{index}"

            keywords = self._normalize_string_list(raw_item.get("keywords"))
            dates = self._normalize_string_list(raw_item.get("dates"))
            if not keywords or not dates:
                continue

            reward_points = self._normalize_int(
                raw_item.get("reward_points"), 0, minimum=0
            )
            if reward_points <= 0:
                continue

            probability = min(
                self._normalize_float(raw_item.get("probability"), 1.0, minimum=0.0),
                1.0,
            )
            entry = {
                "name": name.strip(),
                "enabled": bool(raw_item.get("enabled", True)),
                "priority": self._normalize_int(raw_item.get("priority"), 50, 0),
                "scope": self._normalize_string_list(raw_item.get("scope")),
                "dates": dates,
                "keywords": keywords,
                "reward_points": reward_points,
                "daily_limit_per_user": self._normalize_int(
                    raw_item.get("daily_limit_per_user"), 1, minimum=1
                ),
                "probability": probability,
                "announce": bool(raw_item.get("announce", True)),
                "reply_template": self._normalize_text(raw_item.get("reply_template")),
                "exact_match": bool(raw_item.get("exact_match", False)),
            }
            entries.append(entry)

        entries.sort(key=lambda item: (item["priority"], item["name"]))
        return entries

    def _get_templates(self) -> Dict[str, str]:
        templates = self.config.get("message_templates", {})
        if not isinstance(templates, dict):
            templates = {}

        resolved = DEFAULT_TEMPLATES.copy()
        for key, fallback in DEFAULT_TEMPLATES.items():
            configured = templates.get(key)
            if not isinstance(configured, str) or not configured.strip():
                continue
            if configured == LEGACY_DEFAULT_TEMPLATES.get(key):
                resolved[key] = fallback
            else:
                resolved[key] = configured
        return resolved

    def _format_msg(self, template_key: str, **kwargs: Any) -> str:
        message = self._get_templates().get(
            template_key, DEFAULT_TEMPLATES.get(template_key, "")
        )
        kwargs["name"] = self._get_points_name()
        try:
            return self._single_line_message(str(message).format(**kwargs))
        except Exception:
            logger.warning(f"消息模板 {template_key} 格式异常，已回退到默认模板。")
            fallback = DEFAULT_TEMPLATES.get(template_key, "")
            return self._single_line_message(fallback.format(**kwargs))

    def _get_user_record(self, user_id: str) -> Dict[str, Any]:
        users = self.data.setdefault("users", {})
        if user_id not in users:
            users[user_id] = self._normalize_user_record({})
        return users[user_id]

    def _get_message_segments(self, event: AstrMessageEvent) -> list[Any]:
        return list(getattr(event.message_obj, "message", []) or [])

    def _get_group_id(self, event: AstrMessageEvent) -> str:
        group_id = event.get_group_id()
        if group_id is None:
            return ""
        return str(group_id)

    def _get_sign_in_scope_id(self, event: AstrMessageEvent) -> str:
        return self._get_group_id(event) or GLOBAL_SIGN_IN_SCOPE_ID

    def _get_today_birthday_md(
        self, now: datetime.datetime | None = None
    ) -> str:
        if now is None:
            now = datetime.datetime.now()
        return f"{now.month:02d}/{now.day:02d}"

    def _get_sign_in_business_date(
        self, now: datetime.datetime | None = None
    ) -> datetime.date:
        if now is None:
            now = datetime.datetime.now()
        if now.hour < 4:
            now -= datetime.timedelta(days=1)
        return now.date()

    def _get_sign_in_business_date_str(
        self, now: datetime.datetime | None = None
    ) -> str:
        return self._get_sign_in_business_date(now).isoformat()

    def _get_sender_display_name(self, event: AstrMessageEvent) -> str:
        sender_id = str(event.get_sender_id())
        sender_name = getattr(event, "get_sender_name", lambda: None)()
        return self._safe_display_name(sender_name, sender_id)

    def _get_sender_reply_name(self, event: AstrMessageEvent) -> str:
        sender_name = getattr(event, "get_sender_name", lambda: None)()
        return self._safe_reply_name(sender_name)

    def _collect_user_group_ids(self, user_id: str) -> list[str]:
        groups = self.data.get("groups", {})
        group_ids: list[str] = []
        for group_id, group_info in groups.items():
            if not str(group_id).isdigit() or not isinstance(group_info, dict):
                continue
            members = group_info.get("members", {})
            if isinstance(members, dict) and user_id in members:
                group_ids.append(str(group_id))
        return group_ids

    def _touch_group_member(
        self, event: AstrMessageEvent, user_id: str, display_name: str | None = None
    ) -> bool:
        group_id = self._get_group_id(event)
        if not group_id:
            return False

        groups = self.data.setdefault("groups", {})
        group_info = groups.setdefault(group_id, {"members": {}})
        current_target = self._normalize_text(group_info.get("message_target"))
        event_target = (
            self._normalize_text(getattr(event, "unified_msg_origin", ""))
            if group_id
            else ""
        )
        changed = False
        if event_target and current_target != event_target:
            group_info["message_target"] = event_target
            changed = True
        members = group_info.setdefault("members", {})

        safe_name = self._safe_display_name(display_name, user_id)
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        current_member = members.get(user_id)
        current_display_name = (
            current_member.get("display_name")
            if isinstance(current_member, dict)
            else None
        )
        current_negative_title = (
            self._normalize_text(current_member.get("negative_title"))
            if isinstance(current_member, dict)
            else ""
        )
        if current_display_name == safe_name:
            return changed

        members[user_id] = {
            "display_name": safe_name,
            "updated_at": timestamp,
            "negative_title": current_negative_title,
        }
        return True

    def _extract_target_user_id(self, event: AstrMessageEvent) -> str | None:
        for component in self._get_message_segments(event):
            if isinstance(component, At):
                target_uid = getattr(component, "qq", None) or getattr(
                    component, "user_id", None
                )
                if target_uid:
                    return str(target_uid)
        return None

    def _extract_reply_message_id(self, event: AstrMessageEvent) -> int | None:
        for component in self._get_message_segments(event):
            if isinstance(component, Reply):
                reply_id = getattr(component, "id", None)
                try:
                    return int(reply_id)
                except (TypeError, ValueError):
                    return None
        return None

    def _get_command_args(self, event: AstrMessageEvent) -> str:
        return (event.message_str or "").partition(" ")[2].strip()

    def _get_command_name(self, event: AstrMessageEvent) -> str:
        head = (event.message_str or "").strip().split(maxsplit=1)[0]
        return head.lstrip("/") or "该命令"

    def _ensure_qq_group_exchange(
        self, event: AstrMessageEvent, action_name: str
    ) -> str | None:
        if not self._get_group_id(event):
            return f"{action_name} 仅支持群聊中使用。"
        if not isinstance(event, AiocqhttpMessageEvent):
            return f"{action_name} 当前仅支持 QQ / AIOCQHTTP 平台。"
        return None

    def _parse_datetime(self, value: str) -> datetime.datetime | None:
        if not value:
            return None
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            return None

    def _resolve_lottery_mode(
        self, raw_args: str, lottery_cfg: Dict[str, Any]
    ) -> str:
        first_arg = raw_args.strip().split(maxsplit=1)[0].lower() if raw_args else ""
        if first_arg in {"个人", "单人", "personal", "single"}:
            return "personal"
        if first_arg in {"群体", "团体", "group", "shared"}:
            return "group"
        return lottery_cfg["default_mode"]

    def _is_scope_matched(
        self, scope: list[str], event: AstrMessageEvent, user_id: str
    ) -> bool:
        if not scope:
            return True

        group_id = self._get_group_id(event)
        current_values = {
            user_id,
            f"user:{user_id}",
        }
        if group_id:
            current_values.add(group_id)
            current_values.add(f"group:{group_id}")

        return any(item in current_values for item in scope)

    def _is_special_reward_date_matched(
        self, date_rules: list[str], today: datetime.date
    ) -> bool:
        today_iso = today.isoformat()
        month_day = today.strftime("%m-%d")
        month_day_short = f"{today.month}-{today.day}"

        for raw_rule in date_rules:
            rule = raw_rule.strip()
            if not rule:
                continue
            normalized_rule = rule.replace("/", "-")
            if normalized_rule in {"*", "daily", "everyday"}:
                return True
            if normalized_rule in {today_iso, month_day, month_day_short}:
                return True
        return False

    def _is_special_reward_keyword_matched(
        self, message: str, entry: Dict[str, Any]
    ) -> bool:
        if entry["exact_match"]:
            return any(message == keyword for keyword in entry["keywords"])

        for keyword in entry["keywords"]:
            try:
                if re.search(keyword, message):
                    return True
            except re.error:
                if keyword in message:
                    return True
        return False

    def _format_special_reward_message(self, entry: Dict[str, Any], **kwargs: Any) -> str:
        template = entry["reply_template"].strip()
        if not template:
            template = (
                "{user}触发了活动词条【{entry}】，获得 {points} {name}。"
                "当前共有 {total} {name}。"
            )
        try:
            return self._single_line_message(template.format(**kwargs))
        except Exception:
            logger.warning(f"日期奖励词条 {entry['name']} 的回复模板格式异常，已回退默认模板。")
            return self._single_line_message(
                f"{kwargs['user']}触发了活动词条【{kwargs['entry']}】，获得 "
                f"{kwargs['points']} {kwargs['name']}。当前共有 {kwargs['total']} {kwargs['name']}。"
            )

    def _is_command_like_message(self, message: str) -> bool:
        stripped = message.strip()
        if not stripped:
            return True
        if stripped.startswith(("/", "!", "#", "。", "！")):
            return True
        if self._match_quick_action(stripped):
            return True
        return bool(COMMAND_TEXT_PATTERN.match(stripped))

    def _build_sign_in_bonus_detail(
        self,
        base_points: int,
        first_bonus: int,
        daily_first_bonus: int,
        streak_bonus: int,
        weekly_bonus: int,
    ) -> str:
        detail_parts = [f"基础 {base_points}"]
        if first_bonus:
            detail_parts.append(f"首签 +{first_bonus}")
        if daily_first_bonus:
            detail_parts.append(f"每日首签 +{daily_first_bonus}")
        if streak_bonus:
            detail_parts.append(f"连签 +{streak_bonus}")
        if weekly_bonus:
            detail_parts.append(f"周奖励 +{weekly_bonus}")
        return f"（{'，'.join(detail_parts)}）"

    def _extract_llm_response_text(self, llm_resp: Any) -> str:
        if not llm_resp:
            return ""
        for attr in ("content", "text", "message", "completion_text"):
            value = getattr(llm_resp, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _get_negative_debt_message(self) -> str:
        return "你已背负债务，请穿上女仆装打工。"

    async def _generate_sign_in_fortune_text(
        self,
        event: AstrMessageEvent,
        reply_name: str,
        is_lucky: bool,
        points_delta: int,
        total_points: int,
    ) -> str:
        provider = None
        try:
            provider = self.context.get_using_provider(event.unified_msg_origin)
        except Exception as exc:
            logger.debug(f"获取签到彩蛋 LLM provider 失败: {exc}")

        title = "欧皇" if is_lucky else "非酋"
        direction_text = "额外获得" if is_lucky else "被扣除"
        fallback = (
            f"{reply_name}触发了稀有签到事件【{title}】，"
            f"{direction_text} {points_delta} {self._get_points_name()}。"
            f"当前共有 {total_points} {self._get_points_name()}。"
        )
        if not provider:
            return self._single_line_message(fallback)

        prompt = (
            f"用户 {reply_name} 在签到时触发了一个极低概率事件，身份是“{title}”。"
            f"本次{'增加' if is_lucky else '减少'}了 {points_delta} 点积分，"
            f"当前总积分为 {total_points}。"
            "请用一句到两句简短、有趣、适合群聊的中文回复。"
            "不要使用 markdown，不要解释规则，不要超过 45 个字。"
        )

        try:
            llm_resp = await provider.text_chat(
                prompt=prompt,
                session_id=event.unified_msg_origin,
                persist=False,
            )
            content = self._extract_llm_response_text(llm_resp)
            if content:
                return self._single_line_message(content)
        except Exception as exc:
            logger.warning(f"签到彩蛋 LLM 回复失败，已回退默认文案: {exc}")

        return self._single_line_message(fallback)

    async def _generate_birthday_blessing_text(
        self, event: AstrMessageEvent, reply_name: str, reward_points: int
    ) -> str:
        provider = None
        try:
            provider = self.context.get_using_provider(event.unified_msg_origin)
        except Exception as exc:
            logger.debug(f"获取生日签到 LLM provider 失败: {exc}")

        fallback = (
            f"{reply_name}生日快乐，愿你今天被温柔以待、好运常伴！"
            f"已为你送上 {reward_points} {self._get_points_name()}。"
        )
        if not provider:
            return self._single_line_message(fallback)

        prompt = (
            f"群成员 {reply_name} 发送了“{BIRTHDAY_SIGN_IN_TRIGGER}”，"
            f"系统将发放 {reward_points} 点积分作为生日奖励。"
            "请写一句温暖、自然、适合群聊发送的中文生日祝福。"
            "不要使用 markdown，不要分段，不要超过 45 个字。"
        )
        try:
            llm_resp = await provider.text_chat(
                prompt=prompt,
                session_id=event.unified_msg_origin,
                persist=False,
            )
            content = self._extract_llm_response_text(llm_resp)
            if content:
                return self._single_line_message(content)
        except Exception as exc:
            logger.warning(f"生日签到 LLM 祝福生成失败，已回退默认文案: {exc}")

        return self._single_line_message(fallback)

    async def _try_birthday_sign_in(
        self, event: AstrMessageEvent, message: str
    ) -> str | None:
        if self._normalize_trigger_token(message) != self._normalize_trigger_token(
            BIRTHDAY_SIGN_IN_TRIGGER
        ):
            return None

        user_id = str(event.get_sender_id())
        reply_name = self._get_sender_reply_name(event)
        now = datetime.datetime.now()
        current_year = str(now.year)
        today_md = self._get_today_birthday_md(now)

        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            group_member_changed = self._touch_group_member(
                event, user_id, self._get_sender_display_name(event)
            )
            birthday_md = self._normalize_birthday_md(user_info.get("birthday_md"))
            auto_recorded = False
            if not birthday_md:
                birthday_md = today_md
                user_info["birthday_md"] = birthday_md
                auto_recorded = True

            if birthday_md != today_md:
                if group_member_changed:
                    await self._save_data_locked()
                return (
                    f"{reply_name}记录的生日是 {birthday_md}，今天还不是你的生日，"
                    "先把祝福留到当天吧。"
                )

            if user_info.get("last_birthday_sign_in_year") == current_year:
                if group_member_changed:
                    await self._save_data_locked()
                return f"{reply_name}今年的生日签到奖励已经领过啦，明年再来吧。"

            user_info["points"] += BIRTHDAY_SIGN_IN_REWARD
            user_info["last_birthday_sign_in_year"] = current_year
            total_points = user_info["points"]
            await self._save_data_locked()

        await self._refresh_negative_titles_for_user(event, user_id)

        blessing_text = await self._generate_birthday_blessing_text(
            event, reply_name, BIRTHDAY_SIGN_IN_REWARD
        )
        record_text = (
            f"已自动为你记录生日 {today_md}，" if auto_recorded else ""
        )
        return self._single_line_message(
            f"{record_text}{blessing_text}获得 {BIRTHDAY_SIGN_IN_REWARD} {self._get_points_name()}，"
            f"当前共有 {total_points} {self._get_points_name()}。"
        )

    def _apply_birthday_reward_locked(
        self, user_info: Dict[str, Any], now: datetime.datetime | None = None
    ) -> bool:
        if now is None:
            now = datetime.datetime.now()
        birthday_md = self._normalize_birthday_md(user_info.get("birthday_md"))
        if not birthday_md or birthday_md != self._get_today_birthday_md(now):
            return False
        current_year = str(now.year)
        if self._normalize_text(user_info.get("last_birthday_sign_in_year")) == current_year:
            return False
        user_info["points"] += BIRTHDAY_SIGN_IN_REWARD
        user_info["last_birthday_sign_in_year"] = current_year
        return True

    async def _birthday_broadcast_loop(self) -> None:
        while not self._birthday_broadcast_stop_event.is_set():
            now = datetime.datetime.now()
            if now.hour >= 8:
                await self._run_birthday_broadcast()
                now = datetime.datetime.now()
            next_run = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += datetime.timedelta(days=1)

            wait_seconds = max((next_run - now).total_seconds(), 1)
            try:
                await asyncio.wait_for(
                    self._birthday_broadcast_stop_event.wait(), timeout=wait_seconds
                )
                break
            except asyncio.TimeoutError:
                await self._run_birthday_broadcast()

    async def _run_birthday_broadcast(self) -> None:
        today = datetime.date.today()
        today_iso = today.isoformat()
        today_md = self._get_today_birthday_md(
            datetime.datetime.combine(today, datetime.time.min)
        )

        pending_messages: list[tuple[str, str, str]] = []
        async with self._data_lock:
            users = self.data.setdefault("users", {})
            groups = self.data.setdefault("groups", {})
            for group_id, group_info in groups.items():
                if not isinstance(group_info, dict):
                    continue
                if self._normalize_text(group_info.get("last_birthday_broadcast_date")) == today_iso:
                    continue

                target = self._normalize_text(group_info.get("message_target"))
                if not target:
                    continue

                members = group_info.get("members", {})
                if not isinstance(members, dict):
                    continue

                birthday_names: list[str] = []
                for user_id, member_info in members.items():
                    user_info = users.get(user_id)
                    if not isinstance(user_info, dict):
                        continue
                    if self._normalize_birthday_md(user_info.get("birthday_md")) != today_md:
                        continue
                    display_name = self._safe_display_name(
                        member_info.get("display_name") if isinstance(member_info, dict) else None,
                        str(user_id),
                    )
                    birthday_names.append(display_name)

                if birthday_names:
                    names_text = "、".join(birthday_names)
                    text = f"今日寿星名单：{names_text}，祝大家生日快乐！"
                    pending_messages.append((str(group_id), target, text))

        sent_group_ids: list[str] = []
        for group_id, target, text in pending_messages:
            try:
                await self.context.send_message(target, MessageChain([Plain(text)]))
                sent_group_ids.append(group_id)
            except Exception as exc:
                logger.warning(f"发送生日播报失败: group={group_id}, error={exc}")

        if not sent_group_ids:
            return

        async with self._data_lock:
            groups = self.data.setdefault("groups", {})
            for group_id in sent_group_ids:
                group_info = groups.get(group_id)
                if isinstance(group_info, dict):
                    group_info["last_birthday_broadcast_date"] = today_iso
            await self._save_data_locked()

    def _resolve_fortune_event_type(
        self, user_info: Dict[str, Any], sign_cfg: Dict[str, Any]
    ) -> str | None:
        if not sign_cfg["fortune_event_enabled"] or sign_cfg["fortune_event_points"] <= 0:
            return None

        lucky_threshold = sign_cfg["fortune_lucky_pity_threshold"]
        unlucky_threshold = sign_cfg["fortune_unlucky_pity_threshold"]
        lucky_count = user_info["fortune_lucky_pity_count"]
        unlucky_count = user_info["fortune_unlucky_pity_count"]

        forced_types: list[str] = []
        if sign_cfg["fortune_pity_enabled"]:
            if lucky_threshold > 0 and lucky_count + 1 >= lucky_threshold:
                forced_types.append("lucky")
            if unlucky_threshold > 0 and unlucky_count + 1 >= unlucky_threshold:
                forced_types.append("unlucky")

        if forced_types:
            if len(forced_types) == 1:
                return forced_types[0]
            lucky_ratio = (
                (lucky_count + 1) / lucky_threshold if lucky_threshold > 0 else 0.0
            )
            unlucky_ratio = (
                (unlucky_count + 1) / unlucky_threshold
                if unlucky_threshold > 0
                else 0.0
            )
            if lucky_ratio > unlucky_ratio:
                return "lucky"
            if unlucky_ratio > lucky_ratio:
                return "unlucky"
            return random.choice(forced_types)

        if random.random() < sign_cfg["fortune_event_chance"]:
            return random.choice(["lucky", "unlucky"])
        return None

    def _apply_fortune_pity_progress(
        self, user_info: Dict[str, Any], event_type: str | None
    ) -> None:
        if event_type == "lucky":
            user_info["fortune_lucky_pity_count"] = 0
            user_info["fortune_unlucky_pity_count"] += 1
            return
        if event_type == "unlucky":
            user_info["fortune_unlucky_pity_count"] = 0
            user_info["fortune_lucky_pity_count"] += 1
            return

        user_info["fortune_lucky_pity_count"] += 1
        user_info["fortune_unlucky_pity_count"] += 1

    async def _refresh_negative_titles_for_user(
        self, event: AstrMessageEvent, user_id: str
    ) -> None:
        if not isinstance(event, AiocqhttpMessageEvent):
            return
        if not getattr(event, "bot", None):
            return

        async with self._data_lock:
            group_ids = self._collect_user_group_ids(user_id)
            users = self.data.get("users", {})
            groups = self.data.get("groups", {})
            planned_updates: list[tuple[str, str, str]] = []

            for group_id in group_ids:
                group_info = groups.get(group_id, {})
                members = group_info.get("members", {})
                if not isinstance(members, dict):
                    continue

                negative_user_ids = [
                    member_user_id
                    for member_user_id in members
                    if users.get(member_user_id, {}).get("points", 0) < 0
                ]
                negative_user_ids.sort(
                    key=lambda member_user_id: (
                        users.get(member_user_id, {}).get("points", 0),
                        member_user_id,
                    )
                )

                desired_titles = {
                    member_user_id: f"群女仆{index}号"
                    for index, member_user_id in enumerate(negative_user_ids, start=1)
                }

                for member_user_id, member_info in members.items():
                    if not isinstance(member_info, dict):
                        continue
                    current_title = self._normalize_text(
                        member_info.get("negative_title")
                    )
                    desired_title = desired_titles.get(member_user_id, "")
                    if current_title != desired_title and (current_title or desired_title):
                        planned_updates.append(
                            (str(group_id), str(member_user_id), desired_title)
                        )

        if not planned_updates:
            return

        successful_updates: list[tuple[str, str, str]] = []
        for group_id, member_user_id, desired_title in planned_updates:
            try:
                await event.bot.set_group_special_title(
                    group_id=int(group_id),
                    user_id=int(member_user_id),
                    special_title=desired_title,
                    duration=-1,
                )
                successful_updates.append((group_id, member_user_id, desired_title))
            except Exception as exc:
                logger.warning(
                    f"同步负分头衔失败: group={group_id}, user={member_user_id}, title={desired_title!r}, error={exc}"
                )

        if not successful_updates:
            return

        async with self._data_lock:
            groups = self.data.get("groups", {})
            for group_id, member_user_id, desired_title in successful_updates:
                member_info = (
                    groups.get(group_id, {})
                    .get("members", {})
                    .get(member_user_id)
                )
                if isinstance(member_info, dict):
                    member_info["negative_title"] = desired_title
            await self._save_data_locked()

    def _roll_lottery_prize(self, prizes: list[Dict[str, Any]]) -> tuple[Dict[str, Any], int]:
        total_weight = sum(max(float(item.get("weight", 0.0)), 0.0) for item in prizes)
        if total_weight <= 0:
            fallback = prizes[0]
            return fallback, fallback["min_points"]

        threshold = random.uniform(0, total_weight)
        current = 0.0
        selected = prizes[-1]
        for prize in prizes:
            current += max(float(prize.get("weight", 0.0)), 0.0)
            if threshold <= current:
                selected = prize
                break

        reward_points = random.randint(
            int(selected["min_points"]), int(selected["max_points"])
        )
        return selected, reward_points

    def _calculate_group_lottery_rewards(
        self, total_points: int, ratios: list[float]
    ) -> list[int]:
        total_ratio = sum(ratios)
        if total_points <= 0 or total_ratio <= 0:
            return [0 for _ in ratios]

        raw_rewards = [(total_points * ratio) / total_ratio for ratio in ratios]
        rewards = [int(item) for item in raw_rewards]
        remainder = total_points - sum(rewards)
        order = sorted(
            range(len(raw_rewards)),
            key=lambda index: (raw_rewards[index] - rewards[index], ratios[index]),
            reverse=True,
        )
        for index in order[:remainder]:
            rewards[index] += 1
        return rewards

    def _refund_expired_group_lottery_locked(
        self, group_info: Dict[str, Any], today: str
    ) -> str:
        pool = group_info.setdefault(
            "group_lottery_pool", {"date": "", "participants": []}
        )
        pool_date = self._normalize_text(pool.get("date"))
        participants = pool.get("participants", [])
        if not pool_date or pool_date == today or not isinstance(participants, list):
            return ""
        if not participants:
            pool["date"] = ""
            return ""

        refunded_count = 0
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            user_id = str(participant.get("user_id", "")).strip()
            if not user_id:
                continue
            paid_points = self._normalize_int(participant.get("paid_points"), 0, 0)
            if paid_points <= 0:
                continue
            user_info = self._get_user_record(user_id)
            user_info["points"] += paid_points
            refunded_count += 1

        pool["date"] = ""
        pool["participants"] = []
        if refunded_count <= 0:
            return ""
        return (
            f"上一轮群体抽奖因跨日仍未满员，已自动退还 {refunded_count} 名参与者的报名积分。"
        )

    async def _try_special_date_reward(
        self, event: AstrMessageEvent, message: str
    ) -> str | None:
        entries = self._get_special_date_reward_entries()
        if not entries:
            return None

        today = datetime.date.today()
        today_iso = today.isoformat()
        user_id = str(event.get_sender_id())
        reply_name = self._get_sender_reply_name(event)

        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            group_member_changed = self._touch_group_member(
                event, user_id, self._get_sender_display_name(event)
            )

            for entry in entries:
                if not entry["enabled"]:
                    continue
                if not self._is_scope_matched(entry["scope"], event, user_id):
                    continue
                if not self._is_special_reward_date_matched(entry["dates"], today):
                    continue
                if not self._is_special_reward_keyword_matched(message, entry):
                    continue

                claims = user_info["special_reward_claims"]
                claim = claims.get(entry["name"], {"date": "", "count": 0})
                claim_count = (
                    self._normalize_int(claim.get("count"), 0, 0)
                    if claim.get("date") == today_iso
                    else 0
                )
                if claim_count >= entry["daily_limit_per_user"]:
                    continue
                if entry["probability"] < 1 and random.random() > entry["probability"]:
                    continue

                user_info["points"] += entry["reward_points"]
                claims[entry["name"]] = {
                    "date": today_iso,
                    "count": claim_count + 1,
                }
                await self._save_data_locked()

                if not entry["announce"]:
                    return ""

                return self._format_special_reward_message(
                    entry,
                    user=reply_name,
                    entry=entry["name"],
                    points=entry["reward_points"],
                    total=user_info["points"],
                    date=today_iso,
                    name=self._get_points_name(),
                )

            if group_member_changed:
                await self._save_data_locked()

        return None

    def _parse_manual_points_args(
        self, event: AstrMessageEvent
    ) -> tuple[str | None, int | None]:
        raw_args = self._get_command_args(event)
        if not raw_args:
            return None, None

        amount_match = re.search(r"(-?\d+)\s*$", raw_args)
        if not amount_match:
            return None, None

        try:
            amount = int(amount_match.group(1))
        except ValueError:
            return None, None

        target_part = raw_args[: amount_match.start()].strip()
        target_uid = self._extract_target_user_id(event)
        if not target_uid and target_part:
            uid_match = re.search(r"(\d{5,20})", target_part)
            if uid_match:
                target_uid = uid_match.group(1)

        return target_uid, amount

    async def _ensure_points_admin(self, event: AstrMessageEvent) -> str | None:
        admin_ids = self._get_points_admin_ids()
        if not admin_ids:
            return "当前未配置积分管理员名单，请先在插件配置中填写 admin_settings.points_admin_ids。"

        if str(event.get_sender_id()) not in admin_ids:
            return "你没有积分管理权限。"

        return None

    async def _deduct_sender_points(
        self, event: AstrMessageEvent, cost: int
    ) -> tuple[bool, int]:
        sender_id = str(event.get_sender_id())

        async with self._data_lock:
            user_info = self._get_user_record(sender_id)
            self._touch_group_member(event, sender_id, self._get_sender_display_name(event))

            if user_info["points"] < cost:
                return False, user_info["points"]

            user_info["points"] -= cost
            remaining_points = user_info["points"]
            await self._save_data_locked()

        return True, remaining_points

    async def _refund_sender_points(
        self, event: AstrMessageEvent, amount: int
    ) -> int:
        sender_id = str(event.get_sender_id())

        async with self._data_lock:
            user_info = self._get_user_record(sender_id)
            user_info["points"] += amount
            await self._save_data_locked()
            return user_info["points"]

    def _get_group_rankings(self, group_id: str) -> list[tuple[str, Dict[str, Any], str]]:
        users = self.data.get("users", {})
        groups = self.data.get("groups", {})
        group_info = groups.get(group_id, {})
        members = group_info.get("members", {})

        rankings: list[tuple[str, Dict[str, Any], str]] = []
        for user_id, member_info in members.items():
            user_record = users.get(user_id)
            if not user_record:
                continue
            display_name = self._safe_display_name(
                member_info.get("display_name"), user_id
            )
            rankings.append((user_id, user_record, display_name))

        rankings.sort(key=lambda item: item[1].get("points", 0), reverse=True)
        return rankings

    def _get_global_rankings(self) -> list[tuple[str, Dict[str, Any], str]]:
        users = self.data.get("users", {})
        rankings = [
            (
                user_id,
                user_record,
                self._safe_display_name(None, user_id),
            )
            for user_id, user_record in users.items()
        ]
        rankings.sort(key=lambda item: item[1].get("points", 0), reverse=True)
        return rankings

    async def _handle_sign_in(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        now = datetime.datetime.now()
        today = self._get_sign_in_business_date(now).isoformat()
        yesterday = (self._get_sign_in_business_date(now) - datetime.timedelta(days=1)).isoformat()
        reply_name = self._get_sender_reply_name(event)
        sign_cfg = self._get_sign_in_settings()
        sign_in_scope_id = self._get_sign_in_scope_id(event)
        fortune_triggered = False
        fortune_is_lucky = False
        fortune_points_delta = 0
        birthday_reward_triggered = False

        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            group_member_changed = self._touch_group_member(
                event, user_id, self._get_sender_display_name(event)
            )
            groups = self.data.setdefault("groups", {})
            scope_info = groups.setdefault(
                sign_in_scope_id,
                {
                    "members": {},
                    "group_lottery_pool": {"date": "", "participants": []},
                    "daily_first_sign_in_date": "",
                    "daily_first_sign_in_user_id": "",
                },
            )

            if user_info["last_sign_in"] == today:
                if group_member_changed:
                    await self._save_data_locked()

                yield event.plain_result(
                    self._format_msg(
                        "already_signed_in",
                        user=reply_name,
                        total=user_info["points"],
                        streak=user_info["streak"],
                        total_sign_in_days=user_info["total_sign_in_days"],
                    )
                )
                return

            if sign_cfg["sign_in_mode"] == "fixed":
                base_points = sign_cfg["fixed_sign_in_points"]
            else:
                base_points = random.randint(
                    sign_cfg["min_sign_in_points"], sign_cfg["max_sign_in_points"]
                )
            previous_days = user_info["total_sign_in_days"]
            first_bonus = (
                sign_cfg["first_sign_in_bonus"] if previous_days == 0 else 0
            )
            daily_first_bonus = 0
            if (
                sign_cfg["daily_first_sign_in_bonus"] > 0
                and scope_info.get("daily_first_sign_in_date") != today
            ):
                daily_first_bonus = sign_cfg["daily_first_sign_in_bonus"]
                scope_info["daily_first_sign_in_date"] = today
                scope_info["daily_first_sign_in_user_id"] = user_id

            if user_info["last_sign_in"] == yesterday:
                user_info["streak"] += 1
            else:
                user_info["streak"] = 1

            streak_bonus = 0
            if sign_cfg["streak_bonus_enabled"]:
                streak_bonus = min(
                    max(user_info["streak"] - 1, 0) * sign_cfg["streak_step_bonus"],
                    sign_cfg["streak_bonus_cap"],
                )

            weekly_bonus = 0
            if user_info["streak"] > 0 and user_info["streak"] % 7 == 0:
                weekly_bonus = sign_cfg["weekly_streak_bonus"]

            gain = (
                base_points
                + first_bonus
                + daily_first_bonus
                + streak_bonus
                + weekly_bonus
            )
            user_info["points"] += gain

            fortune_event_type = self._resolve_fortune_event_type(user_info, sign_cfg)
            if fortune_event_type:
                fortune_triggered = True
                fortune_is_lucky = fortune_event_type == "lucky"
                if fortune_is_lucky:
                    user_info["points"] += sign_cfg["fortune_event_points"]
                    fortune_points_delta = sign_cfg["fortune_event_points"]
                else:
                    before_deduction = user_info["points"]
                    user_info["points"] = max(
                        0, user_info["points"] - sign_cfg["fortune_event_points"]
                    )
                    fortune_points_delta = before_deduction - user_info["points"]

            self._apply_fortune_pity_progress(user_info, fortune_event_type)
            birthday_reward_triggered = self._apply_birthday_reward_locked(user_info, now)

            user_info["last_sign_in"] = today
            user_info["total_sign_in_days"] = previous_days + 1
            if not user_info["first_sign_in_at"]:
                user_info["first_sign_in_at"] = today

            await self._save_data_locked()

            total_points = user_info["points"]
            streak = user_info["streak"]
            total_sign_in_days = user_info["total_sign_in_days"]

        fortune_text = ""
        if fortune_triggered and fortune_points_delta > 0:
            fortune_text = await self._generate_sign_in_fortune_text(
                event,
                reply_name,
                fortune_is_lucky,
                fortune_points_delta,
                total_points,
            )
        birthday_text = ""
        if birthday_reward_triggered:
            blessing_text = await self._generate_birthday_blessing_text(
                event, reply_name, BIRTHDAY_SIGN_IN_REWARD
            )
            birthday_text = (
                f"{blessing_text}获得 {BIRTHDAY_SIGN_IN_REWARD} {self._get_points_name()}，"
                f"当前共有 {total_points} {self._get_points_name()}。"
            )

        await self._refresh_negative_titles_for_user(event, user_id)

        yield event.plain_result(
            self._single_line_message(
                self._format_msg(
                "sign_in_success",
                user=reply_name,
                points=gain,
                total=total_points,
                streak=streak,
                total_sign_in_days=total_sign_in_days,
                base_points=base_points,
                first_bonus=first_bonus,
                daily_first_bonus=daily_first_bonus,
                streak_bonus=streak_bonus,
                weekly_bonus=weekly_bonus,
                bonus_detail=self._build_sign_in_bonus_detail(
                    base_points,
                    first_bonus,
                    daily_first_bonus,
                    streak_bonus,
                    weekly_bonus,
                ),
                )
                + fortune_text
                + birthday_text
            )
        )

    @filter.command("签到")
    async def sign_in(self, event: AstrMessageEvent):
        """每日签到以获取积分奖励。"""
        async for result in self._handle_sign_in(event):
            yield result

    @filter.command("生日签到")
    async def birthday_sign_in(self, event: AstrMessageEvent):
        """领取生日签到奖励；未记录生日时会自动记录为今天。"""
        message = self._normalize_text(event.message_str or BIRTHDAY_SIGN_IN_TRIGGER)
        birthday_message = await self._try_birthday_sign_in(event, message)
        if birthday_message is None:
            birthday_message = f"请发送 {BIRTHDAY_SIGN_IN_TRIGGER} 来领取生日签到奖励。"
        yield event.plain_result(birthday_message)

    @filter.command("记录生日")
    async def record_birthday(self, event: AstrMessageEvent):
        """记录生日，格式：/记录生日 10/24"""
        raw_value = self._get_command_args(event)
        birthday_md = self._normalize_birthday_md(raw_value)
        if not birthday_md:
            yield event.plain_result("用法：/记录生日 10/24")
            return

        user_id = str(event.get_sender_id())
        reply_name = self._get_sender_reply_name(event)
        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            self._touch_group_member(event, user_id, self._get_sender_display_name(event))
            user_info["birthday_md"] = birthday_md
            await self._save_data_locked()

        yield event.plain_result(f"{reply_name}的生日已记录为 {birthday_md}。")

    @filter.command("我的积分")
    async def query_points(self, event: AstrMessageEvent):
        """查询自己当前拥有的积分总额。"""
        user_id = str(event.get_sender_id())
        reply_name = self._get_sender_reply_name(event)
        today = self._get_sign_in_business_date_str()

        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            group_member_changed = self._touch_group_member(
                event, user_id, self._get_sender_display_name(event)
            )
            total_points = user_info["points"]
            streak = user_info["streak"]
            total_sign_in_days = user_info["total_sign_in_days"]
            sign_in_status = (
                "今日已签到" if user_info["last_sign_in"] == today else "今日未签到"
            )
            if group_member_changed:
                await self._save_data_locked()

        yield event.plain_result(
            self._format_msg(
                "query_points",
                user=reply_name,
                total=total_points,
                streak=streak,
                total_sign_in_days=total_sign_in_days,
                sign_in_status=sign_in_status,
            )
        )

    @filter.command("积分规则", alias={"星缘积分规则"})
    async def points_rules(self, event: AstrMessageEvent):
        """查看当前积分获取规则。"""
        points_name = self._get_points_name()
        sign_cfg = self._get_sign_in_settings()
        activity_cfg = self._get_activity_settings()
        lottery_cfg = self._get_lottery_settings()
        special_reward_entries = self._get_special_date_reward_entries()
        sign_in_triggers = self._get_sign_in_triggers()
        lottery_triggers = self._get_lottery_triggers()
        sign_in_examples = " / ".join(f"“{item}”" for item in sign_in_triggers[:2])
        lottery_examples = " / ".join(f"“{item}”" for item in lottery_triggers[:2])

        lines = [
            f"【{points_name}获取规则】",
            (
                f"1. 每日签到：基础奖励固定为 {sign_cfg['fixed_sign_in_points']} {points_name}"
                if sign_cfg["sign_in_mode"] == "fixed"
                else (
                    f"1. 每日签到：基础奖励 "
                    f"{sign_cfg['min_sign_in_points']}~{sign_cfg['max_sign_in_points']} {points_name}"
                )
            ),
            f"2. 首次签到：额外 +{sign_cfg['first_sign_in_bonus']} {points_name}",
            (
                f"3. 每日首签：额外 +{sign_cfg['daily_first_sign_in_bonus']} {points_name}，"
                "每日 04:00 刷新"
            ),
            (
                f"4. 连续签到：从第 2 天起每天额外 +{sign_cfg['streak_step_bonus']} "
                f"{points_name}，上限 +{sign_cfg['streak_bonus_cap']}"
            ),
            (
                f"5. 每连续 7 天签到：额外 +{sign_cfg['weekly_streak_bonus']} {points_name}"
            ),
            (
                f"6. 稀有彩蛋：签到时有 {sign_cfg['fortune_event_chance'] * 100:.3f}% 概率触发"
                f"欧皇/非酋事件，额外 +{sign_cfg['fortune_event_points']} 或 -{sign_cfg['fortune_event_points']} {points_name}"
                if sign_cfg["fortune_event_enabled"] and sign_cfg["fortune_event_points"] > 0
                else "6. 稀有彩蛋：当前未开启"
            ),
            (
                "7. 彩蛋保底："
                f"欧皇 {sign_cfg['fortune_lucky_pity_threshold']} 次未触发后保底，"
                f"非酋 {sign_cfg['fortune_unlucky_pity_threshold']} 次未触发后保底"
                if sign_cfg["fortune_pity_enabled"]
                and (
                    sign_cfg["fortune_lucky_pity_threshold"] > 0
                    or sign_cfg["fortune_unlucky_pity_threshold"] > 0
                )
                else "7. 彩蛋保底：当前未开启"
            ),
        ]

        if activity_cfg["enabled"] and activity_cfg["points_per_message"] > 0:
            lines.append(
                "8. 群聊活跃：发送不少于 "
                f"{activity_cfg['min_text_length']} 字的非指令消息，"
                f"每 {activity_cfg['cooldown_seconds']} 秒最多获得一次，"
                f"每天最多 {activity_cfg['daily_limit']} 次，"
                f"每次 +{activity_cfg['points_per_message']} {points_name}"
            )
        else:
            lines.append("8. 群聊活跃奖励：当前未开启")

        lines.append(f"9. 无前缀签到：发送 {sign_in_examples} 也可以直接签到")
        lines.append(
            f"10. 生日签到：发送“{BIRTHDAY_SIGN_IN_TRIGGER}”可获得 {BIRTHDAY_SIGN_IN_REWARD} {points_name}，每人每年一次"
        )

        if lottery_cfg["enabled"]:
            mode_lines: list[str] = []
            if lottery_cfg["personal_enabled"]:
                mode_lines.append(
                    f"个人抽奖每次 {lottery_cfg['personal_cost']} {points_name}，"
                    f"每人每天 {lottery_cfg['personal_daily_limit']} 次"
                )
            if lottery_cfg["group_enabled"]:
                mode_lines.append(
                    f"群体抽奖每次 {lottery_cfg['group_cost']} {points_name}，"
                    f"每人每天 {lottery_cfg['group_daily_limit_per_user']} 次，"
                    f"满 {lottery_cfg['group_required_participants']} 人开奖"
                )
            lines.append(
                "11. 积分抽奖："
                + "；".join(mode_lines)
                + f"；默认模式：{'个人抽奖' if lottery_cfg['default_mode'] == 'personal' else '群体抽奖'}"
            )
            lines.append(f"12. 无前缀抽奖：发送 {lottery_examples} 也可直接参与默认模式抽奖")
        if special_reward_entries:
            enabled_entry_count = len(
                [entry for entry in special_reward_entries if entry["enabled"]]
            )
            lines.append(f"13. 日期口令奖励：当前启用 {enabled_entry_count} 条词条")
        lines.append(
            "14. 负分规则：负分用户只能通过每日签到恢复积分，无法参与抽奖；"
            "在已记录群聊中会自动佩戴“群女仆X号”头衔，转正后自动移除。"
        )
        yield event.plain_result("；".join(lines))

    @filter.command("抽奖")
    async def lottery(self, event: AstrMessageEvent):
        """消耗积分进行一次抽奖。"""
        lottery_cfg = self._get_lottery_settings()
        points_name = self._get_points_name()

        if not lottery_cfg["enabled"]:
            yield event.plain_result("当前未开启积分抽奖功能。")
            return

        mode = self._resolve_lottery_mode(self._get_command_args(event), lottery_cfg)
        if mode == "personal" and not lottery_cfg["personal_enabled"]:
            yield event.plain_result("当前未开启个人抽奖，请使用 /抽奖 群体 或在配置中打开个人抽奖开关。")
            return
        if mode == "group" and not lottery_cfg["group_enabled"]:
            yield event.plain_result("当前未开启群体抽奖，请使用 /抽奖 个人 或在配置中打开群体抽奖开关。")
            return

        group_id = self._get_group_id(event)
        if mode == "group" and not group_id:
            yield event.plain_result("群体抽奖仅支持群聊中使用。")
            return

        user_id = str(event.get_sender_id())
        today = datetime.date.today().isoformat()
        reply_name = self._get_sender_reply_name(event)
        message = ""

        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            group_member_changed = self._touch_group_member(
                event, user_id, self._get_sender_display_name(event)
            )
            if group_member_changed:
                await self._save_data_locked()
            current_points = user_info["points"]

        if current_points < 0:
            yield event.plain_result(
                f"{reply_name}{self._get_negative_debt_message()}当前只能通过每日签到恢复积分，暂时无法参与抽奖。"
            )
            return

        async with self._data_lock:
            user_info = self._get_user_record(user_id)

            if mode == "personal":
                user_draw_times = (
                    user_info["daily_personal_lottery_times"]
                    if user_info["last_personal_lottery_date"] == today
                    else 0
                )

                if user_draw_times >= lottery_cfg["personal_daily_limit"]:
                    if group_member_changed:
                        await self._save_data_locked()
                    message = (
                        f"{reply_name}今天已经抽过个人奖啦，"
                        f"个人抽奖每天最多 {lottery_cfg['personal_daily_limit']} 次。"
                    )
                elif user_info["points"] < lottery_cfg["personal_cost"]:
                    if group_member_changed:
                        await self._save_data_locked()
                    message = (
                        f"{reply_name}的{points_name}不足，个人抽奖需要 "
                        f"{lottery_cfg['personal_cost']} {points_name}，"
                        f"当前仅有 {user_info['points']} {points_name}。"
                    )
                else:
                    prize, reward_points = self._roll_lottery_prize(
                        lottery_cfg["personal_prizes"]
                    )
                    user_info["points"] = (
                        user_info["points"] - lottery_cfg["personal_cost"] + reward_points
                    )
                    user_info["last_personal_lottery_date"] = today
                    user_info["daily_personal_lottery_times"] = user_draw_times + 1
                    user_info["lottery_draw_count"] += 1
                    user_info["lottery_points_spent"] += lottery_cfg["personal_cost"]
                    user_info["lottery_points_won"] += reward_points
                    await self._save_data_locked()

                    net_change = reward_points - lottery_cfg["personal_cost"]
                    net_text = (
                        f"净赚 {net_change}"
                        if net_change >= 0
                        else f"净变化 {net_change}"
                    )
                    message = (
                        f"{reply_name}在个人抽奖中抽中了{prize['label']}，获得 {reward_points} {points_name}。"
                        f"本次消耗 {lottery_cfg['personal_cost']} {points_name}，"
                        f"{net_text} {points_name}，"
                        f"当前余额 {user_info['points']} {points_name}。"
                    )
            else:
                groups = self.data.setdefault("groups", {})
                group_info = groups.setdefault(
                    group_id,
                    {"members": {}, "group_lottery_pool": {"date": "", "participants": []}},
                )
                refund_notice = self._refund_expired_group_lottery_locked(
                    group_info, today
                )
                pool = group_info.setdefault(
                    "group_lottery_pool", {"date": "", "participants": []}
                )
                if pool.get("date") != today:
                    pool["date"] = today
                    pool["participants"] = []

                group_join_times = (
                    user_info["daily_group_lottery_join_times"]
                    if user_info["last_group_lottery_join_date"] == today
                    else 0
                )
                participants = pool.setdefault("participants", [])
                already_joined = any(
                    isinstance(item, dict)
                    and str(item.get("user_id", "")).strip() == user_id
                    for item in participants
                )

                if already_joined or (
                    group_join_times >= lottery_cfg["group_daily_limit_per_user"]
                ):
                    if group_member_changed or refund_notice:
                        await self._save_data_locked()
                    message = (
                        f"{reply_name}今天已经参与过群体抽奖啦，"
                        f"每人每天最多参与 {lottery_cfg['group_daily_limit_per_user']} 次。"
                    )
                elif user_info["points"] < lottery_cfg["group_cost"]:
                    if group_member_changed or refund_notice:
                        await self._save_data_locked()
                    message = (
                        f"{reply_name}的{points_name}不足，群体抽奖需要 "
                        f"{lottery_cfg['group_cost']} {points_name}，"
                        f"当前仅有 {user_info['points']} {points_name}。"
                    )
                else:
                    user_info["points"] -= lottery_cfg["group_cost"]
                    user_info["last_group_lottery_join_date"] = today
                    user_info["daily_group_lottery_join_times"] = group_join_times + 1
                    participants.append(
                        {
                            "user_id": user_id,
                            "display_name": self._get_sender_display_name(event),
                            "paid_points": lottery_cfg["group_cost"],
                            "joined_at": datetime.datetime.now().isoformat(
                                timespec="seconds"
                            ),
                        }
                    )

                    participant_count = len(participants)
                    required_count = lottery_cfg["group_required_participants"]
                    if participant_count < required_count:
                        await self._save_data_locked()
                        prefix = f"{refund_notice}；" if refund_notice else ""
                        message = (
                            f"{prefix}{reply_name}已加入群体抽奖池，扣除 "
                            f"{lottery_cfg['group_cost']} {points_name}。"
                            f"当前人数 {participant_count}/{required_count}，"
                            "凑齐后将按配置比例分配奖池。"
                        )
                    else:
                        shuffled_participants = list(participants)
                        random.shuffle(shuffled_participants)
                        total_pool_points = sum(
                            self._normalize_int(item.get("paid_points"), 0, 0)
                            for item in shuffled_participants
                        )
                        rewards = self._calculate_group_lottery_rewards(
                            total_pool_points,
                            lottery_cfg["group_distribution_ratios"],
                        )

                        results: list[tuple[str, int]] = []
                        for participant, reward_points in zip(
                            shuffled_participants, rewards
                        ):
                            target_user_id = str(participant["user_id"])
                            target_user = self._get_user_record(target_user_id)
                            paid_points = self._normalize_int(
                                participant.get("paid_points"),
                                lottery_cfg["group_cost"],
                                0,
                            )
                            target_user["points"] += reward_points
                            target_user["lottery_draw_count"] += 1
                            target_user["lottery_points_spent"] += paid_points
                            target_user["lottery_points_won"] += reward_points
                            results.append(
                                (
                                    self._safe_display_name(
                                        participant.get("display_name"), target_user_id
                                    ),
                                    reward_points,
                                )
                            )

                        pool["participants"] = []
                        await self._save_data_locked()

                        results.sort(key=lambda item: item[1], reverse=True)
                        lines = []
                        if refund_notice:
                            lines.append(refund_notice)
                        lines.append(
                            f"群体抽奖已满 {required_count} 人并开奖，总奖池 {total_pool_points} {points_name}。"
                        )
                        for index, (display_name, reward_points) in enumerate(
                            results, start=1
                        ):
                            lines.append(
                                f"第{index}位：{display_name}，获得 {reward_points} {points_name}"
                            )
                        message = "；".join(lines)

        yield event.plain_result(self._single_line_message(message))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message_gain_points(self, event: AstrMessageEvent):
        """处理无前缀签到、日期口令奖励与群聊活跃奖励。"""
        message = (event.message_str or "").strip()
        quick_action = self._match_quick_action(message)
        if quick_action == "sign_in":
            event.stop_event()
            async for result in self._handle_sign_in(event):
                yield result.stop_event()
            return
        if quick_action == "lottery":
            event.stop_event()
            async for result in self.lottery(event):
                yield result.stop_event()
            return

        user_id = str(event.get_sender_id())
        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            group_member_changed = self._touch_group_member(
                event, user_id, self._get_sender_display_name(event)
            )
            is_negative_user = user_info["points"] < 0
            if group_member_changed:
                await self._save_data_locked()

        birthday_sign_in_message = await self._try_birthday_sign_in(event, message)
        if birthday_sign_in_message is not None:
            event.stop_event()
            yield event.plain_result(birthday_sign_in_message).stop_event()
            return

        if is_negative_user:
            return

        if message and not self._is_command_like_message(message):
            special_reward_message = await self._try_special_date_reward(event, message)
            if special_reward_message is not None:
                if special_reward_message:
                    yield event.plain_result(special_reward_message)
                return

        activity_cfg = self._get_activity_settings()
        if not activity_cfg["enabled"] or activity_cfg["points_per_message"] <= 0:
            return

        group_id = self._get_group_id(event)
        if not group_id:
            return

        if not message or self._is_command_like_message(message):
            return

        if len(message) < activity_cfg["min_text_length"]:
            return

        now = datetime.datetime.now()
        today = now.date().isoformat()

        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            group_member_changed = self._touch_group_member(
                event, user_id, self._get_sender_display_name(event)
            )

            daily_times = user_info["daily_active_point_times"]
            if user_info["last_active_reward_date"] != today:
                daily_times = 0

            last_reward_at = self._parse_datetime(user_info["last_active_reward_at"])
            within_cooldown = (
                last_reward_at is not None
                and (now - last_reward_at).total_seconds()
                < activity_cfg["cooldown_seconds"]
            )

            if daily_times >= activity_cfg["daily_limit"] or within_cooldown:
                if group_member_changed:
                    await self._save_data_locked()
                return

            user_info["points"] += activity_cfg["points_per_message"]
            user_info["activity_points"] += activity_cfg["points_per_message"]
            user_info["last_active_reward_at"] = now.isoformat(timespec="seconds")
            user_info["last_active_reward_date"] = today
            user_info["daily_active_point_times"] = daily_times + 1
            await self._save_data_locked()

    @filter.command("积分榜")
    async def leaderboard(self, event: AstrMessageEvent):
        """查看本群积分排名前列的群友。"""
        points_name = self._get_points_name()
        limit, show_self_rank = self._get_leaderboard_settings()
        sender_id = str(event.get_sender_id())
        group_id = self._get_group_id(event)

        async with self._data_lock:
            group_member_changed = self._touch_group_member(
                event, sender_id, self._get_sender_display_name(event)
            )
            if group_member_changed:
                await self._save_data_locked()

            if group_id:
                rankings = self._get_group_rankings(group_id)
                title = f"🏆 【本群{points_name}排行榜】 🏆"
                fallback_to_global = not rankings
            else:
                rankings = []
                title = f"🏆 【{points_name}总排行榜】 🏆"
                fallback_to_global = True

            if fallback_to_global:
                rankings = self._get_global_rankings()
                if group_id:
                    title = (
                        f"🏆 【本群{points_name}排行榜】 🏆。"
                        "当前群聊还没有独立排行数据，先展示全局排行。"
                    )

        lines = [title]
        sender_rank = -1

        for index, (user_id, user_info, display_name) in enumerate(rankings, start=1):
            if user_id == sender_id:
                sender_rank = index

            if index <= limit:
                lines.append(
                    f"第{index}名: {display_name} - {user_info['points']} {points_name}"
                )

        if len(lines) == 1:
            lines.append("暂无排行数据")

        if show_self_rank:
            if sender_rank != -1:
                lines.append(f"您的当前排名：第 {sender_rank} 名")
            else:
                lines.append("您暂未上榜")

        yield event.plain_result("；".join(lines))

    @filter.command("兑换头衔")
    async def exchange_title(self, event: AstrMessageEvent):
        """消耗积分兑换自己的群头衔。用法：/兑换头衔 头衔内容"""
        exchange_cfg = self._get_exchange_settings()
        points_name = self._get_points_name()

        if not exchange_cfg["title_enabled"]:
            yield event.plain_result("当前未开启积分兑换头衔功能。")
            return

        err = self._ensure_qq_group_exchange(event, "兑换头衔")
        if err:
            yield event.plain_result(err)
            return

        raw_title = " ".join(self._get_command_args(event).split())
        if not raw_title:
            yield event.plain_result("用法：/兑换头衔 头衔内容")
            return

        if len(raw_title) > exchange_cfg["title_max_length"]:
            yield event.plain_result(
                f"头衔长度不能超过 {exchange_cfg['title_max_length']} 个字符。"
            )
            return

        success, remaining_points = await self._deduct_sender_points(
            event, exchange_cfg["title_cost"]
        )
        if not success:
            yield event.plain_result(
                f"积分不足，兑换头衔需要 {exchange_cfg['title_cost']} {points_name}，"
                f"您当前仅有 {remaining_points} {points_name}。"
            )
            return

        try:
            await event.bot.set_group_special_title(
                group_id=int(event.get_group_id()),
                user_id=int(event.get_sender_id()),
                special_title=raw_title,
                duration=-1,
            )
        except Exception as exc:
            refunded_points = await self._refund_sender_points(
                event, exchange_cfg["title_cost"]
            )
            logger.warning(f"积分兑换头衔失败，已自动退款: {exc}")
            yield event.plain_result(
                f"兑换头衔失败，已退还 {exchange_cfg['title_cost']} {points_name}。"
                f"当前余额：{refunded_points} {points_name}。"
            )
            return

        yield event.plain_result(
            f"兑换成功，已将您的群头衔设置为【{raw_title}】。"
            f"消耗 {exchange_cfg['title_cost']} {points_name}，剩余 {remaining_points} {points_name}。"
        )

    @filter.command("兑换设精")
    async def exchange_essence(self, event: AstrMessageEvent):
        """消耗积分将引用消息设为群精华。用法：回复消息后发送 /兑换设精"""
        exchange_cfg = self._get_exchange_settings()
        points_name = self._get_points_name()

        if not exchange_cfg["essence_enabled"]:
            yield event.plain_result("当前未开启积分兑换设精功能。")
            return

        err = self._ensure_qq_group_exchange(event, "兑换设精")
        if err:
            yield event.plain_result(err)
            return

        reply_message_id = self._extract_reply_message_id(event)
        if reply_message_id is None:
            yield event.plain_result("请先引用一条消息，再发送 /兑换设精。")
            return

        success, remaining_points = await self._deduct_sender_points(
            event, exchange_cfg["essence_cost"]
        )
        if not success:
            yield event.plain_result(
                f"积分不足，兑换设精需要 {exchange_cfg['essence_cost']} {points_name}，"
                f"您当前仅有 {remaining_points} {points_name}。"
            )
            return

        try:
            await event.bot.set_essence_msg(message_id=reply_message_id)
        except Exception as exc:
            refunded_points = await self._refund_sender_points(
                event, exchange_cfg["essence_cost"]
            )
            logger.warning(f"积分兑换设精失败，已自动退款: {exc}")
            yield event.plain_result(
                f"兑换设精失败，已退还 {exchange_cfg['essence_cost']} {points_name}。"
                f"当前余额：{refunded_points} {points_name}。"
            )
            return

        yield event.plain_result(
            f"兑换成功，目标消息已设为精华。"
            f"消耗 {exchange_cfg['essence_cost']} {points_name}，剩余 {remaining_points} {points_name}。"
        )

    @filter.command("兑换禁言")
    async def exchange_mute(self, event: AstrMessageEvent):
        """消耗积分兑换禁言。默认禁自己；配置允许后可 @他人。"""
        exchange_cfg = self._get_exchange_settings()
        points_name = self._get_points_name()

        if not exchange_cfg["mute_enabled"]:
            yield event.plain_result("当前未开启积分兑换禁言功能。")
            return

        err = self._ensure_qq_group_exchange(event, "兑换禁言")
        if err:
            yield event.plain_result(err)
            return

        target_uid = self._extract_target_user_id(event)
        if target_uid and not exchange_cfg["allow_mute_others"]:
            yield event.plain_result(
                "当前配置只允许兑换自禁，若要禁言他人，请在配置中开启 allow_mute_others。"
            )
            return

        if not target_uid:
            target_uid = str(event.get_sender_id())

        if target_uid == str(getattr(event, "get_self_id", lambda: "")()):
            yield event.plain_result("不能对机器人本身使用兑换禁言。")
            return

        success, remaining_points = await self._deduct_sender_points(
            event, exchange_cfg["mute_cost"]
        )
        if not success:
            yield event.plain_result(
                f"积分不足，兑换禁言需要 {exchange_cfg['mute_cost']} {points_name}，"
                f"您当前仅有 {remaining_points} {points_name}。"
            )
            return

        try:
            await event.bot.set_group_ban(
                group_id=int(event.get_group_id()),
                user_id=int(target_uid),
                duration=exchange_cfg["mute_duration_seconds"],
            )
        except Exception as exc:
            refunded_points = await self._refund_sender_points(
                event, exchange_cfg["mute_cost"]
            )
            logger.warning(f"积分兑换禁言失败，已自动退款: {exc}")
            yield event.plain_result(
                f"兑换禁言失败，已退还 {exchange_cfg['mute_cost']} {points_name}。"
                f"当前余额：{refunded_points} {points_name}。"
            )
            return

        target_desc = (
            "自己" if target_uid == str(event.get_sender_id()) else f"用户 {target_uid}"
        )
        yield event.plain_result(
            f"兑换成功，已禁言{target_desc} {exchange_cfg['mute_duration_seconds']} 秒。"
            f"消耗 {exchange_cfg['mute_cost']} {points_name}，剩余 {remaining_points} {points_name}。"
        )

    @filter.command("给积分")
    async def give_points(self, event: AstrMessageEvent):
        """（积分管理员）为指定用户增加积分。用法：/给积分 @用户 数量 或 /给积分 QQ号 数量"""
        async for result in self._admin_modify_points(event, is_add=True):
            yield result

    @filter.command("扣积分")
    async def take_points(self, event: AstrMessageEvent):
        """（积分管理员）扣除指定用户的积分。用法：/扣积分 @用户 数量 或 /扣积分 QQ号 数量"""
        async for result in self._admin_modify_points(event, is_add=False):
            yield result

    async def _admin_modify_points(self, event: AstrMessageEvent, is_add: bool):
        """积分管理员修改积分的统一处理函数"""
        permission_error = await self._ensure_points_admin(event)
        if permission_error:
            yield event.plain_result(permission_error)
            return

        log_operations, max_limit = self._get_admin_settings()
        points_name = self._get_points_name()
        command_name = self._get_command_name(event)
        target_uid, amount = self._parse_manual_points_args(event)

        if amount is None or not target_uid:
            yield event.plain_result(
                f"用法：{command_name} @用户 数量；或：{command_name} QQ号 数量"
            )
            return

        if amount <= 0:
            yield event.plain_result("错误：数值必须是正整数。")
            return

        if amount > max_limit:
            yield event.plain_result(
                f"错误：单次操作不能超过 {max_limit} {points_name}。"
            )
            return

        async with self._data_lock:
            user_info = self._get_user_record(target_uid)
            before_points = user_info["points"]

            if is_add:
                user_info["points"] += amount
                action_str = "增加"
            else:
                user_info["points"] -= amount
                action_str = "扣除"

            self._touch_group_member(event, target_uid)
            await self._save_data_locked()
            current_points = user_info["points"]

        if log_operations:
            logger.info(
                f"管理员 {event.get_sender_id()} 为用户 {target_uid} {action_str}了 "
                f"{amount} {points_name}，积分 {before_points} -> {current_points}"
            )

        await self._refresh_negative_titles_for_user(event, target_uid)

        yield event.plain_result(
            f"成功为用户 {target_uid} {action_str}了 {amount} {points_name}。"
            f"该用户当前总积分为：{current_points}"
        )

    async def terminate(self):
        """插件卸载时保存一次数据"""
        self._backup_stop_event.set()
        self._birthday_broadcast_stop_event.set()
        if self._backup_task is not None:
            self._backup_task.cancel()
            try:
                await self._backup_task
            except asyncio.CancelledError:
                pass
        if self._birthday_broadcast_task is not None:
            self._birthday_broadcast_task.cancel()
            try:
                await self._birthday_broadcast_task
            except asyncio.CancelledError:
                pass

        async with self._data_lock:
            try:
                await self._save_data_locked()
                logger.info("积分系统数据已安全保存。")
            except Exception as exc:
                logger.error(f"卸载保存积分数据失败: {exc}")
