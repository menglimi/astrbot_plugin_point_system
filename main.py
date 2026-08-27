import asyncio
import datetime
import hashlib
import hmac
import inspect
import json
import os
import random
import re
import shutil
import uuid
from typing import Any, Dict

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Image, Plain, Reply, Video
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

try:
    from .birthday_feature import BirthdayFeatureMixin
    from .lottery_feature import LotteryFeatureMixin
except ImportError:
    from birthday_feature import BirthdayFeatureMixin
    from lottery_feature import LotteryFeatureMixin

PLUGIN_NAME = "astrbot_plugin_point_system"
DATA_VERSION = 13
POINT_SNAPSHOT_BUCKET_MINUTES = 15
POINT_SNAPSHOT_RETENTION_DAYS = 90
POINT_SNAPSHOT_MAX_RECORDS = 10000
DEFAULT_POINTS_NAME = "积分"
GLOBAL_SIGN_IN_SCOPE_ID = "__global_sign_in__"
PRIVATE_SEND_SUCCESS = "success"
PRIVATE_SEND_FAILED = "failed"
PRIVATE_SEND_UNCERTAIN = "uncertain"
DEFAULT_NEGATIVE_DEBT_MESSAGE = "你已背负债务，请穿上女仆装打工。"
NEGATIVE_TITLE_RETRY_SECONDS = 6 * 60 * 60
MAX_SPECIAL_REWARD_REGEX_LENGTH = 64
MAX_SPECIAL_REWARD_MESSAGE_LENGTH = 200
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
PREVIOUS_DEFAULT_TEMPLATES = {
    "sign_in_success": "{user}签到成功，{points} {name}到账啦{bonus_detail}，现在一共有 {total} {name}，已连签 {streak} 天，累计签到 {total_sign_in_days} 天。",
    "already_signed_in": "{user}今天已经签到过啦，现在有 {total} {name}，已连签 {streak} 天，累计签到 {total_sign_in_days} 天。",
    "query_points": "{user}现在有 {total} {name}，已连签 {streak} 天，累计签到 {total_sign_in_days} 天，今日状态：{sign_in_status}。",
}
DEFAULT_TEMPLATES = {
    "sign_in_success": "{user}来签到了，拿到 {points} {name}{bonus_detail}，现在攒到 {total} {name} 了，已连签 {streak} 天，累计签到 {total_sign_in_days} 天。",
    "already_signed_in": "{user}今天已经签过到啦，现在有 {total} {name}，已连签 {streak} 天，累计签到 {total_sign_in_days} 天。",
    "query_points": "{user}这边查到你现在有 {total} {name}，已连签 {streak} 天，累计签到 {total_sign_in_days} 天，今日状态是 {sign_in_status}。",
}
COMMAND_PREFIXES = ("/", "!", "#", "。", "！", "／")
MESSAGE_ID_SUFFIX_PATTERN = re.compile(
    r"(?:\s*\[MSG_ID:\s*\d+\])+\s*$",
    re.IGNORECASE,
)
REGISTERED_COMMAND_NAMES = (
    "清空所有数据",
    "兑换头衔",
    "兑换设精",
    "兑换禁言",
    "兑换列表",
    "兑换",
    "记录生日",
    "生日签到",
    "群聊签到",
    "补签",
    "我的积分",
    "积分规则",
    "积分榜",
    "给积分",
    "扣积分",
    "偷积分",
    "积分红包",
    "发红包",
    "抢红包",
    "领红包",
    "红包",
    "抽奖",
)
REGISTERED_COMMAND_NAMES_BY_LENGTH = tuple(
    sorted(REGISTERED_COMMAND_NAMES, key=len, reverse=True)
)


@register(
    PLUGIN_NAME,
    "menglimi",
    "astrbot_plugin_point_system 是一个面向 AstrBot 群聊场景的积分互动插件，围绕“签到、活跃、抽奖、兑换、管理”这几类高频玩法设计。它支持按群维护成员信息、自动保存数据、定时备份、日期口令奖励，以及负分限制和群头衔联动，适合做群活跃体系或轻量积分经济。",
    "2.4.0",
    "https://github.com/menglimi/astrbot_plugin_point_system",
)
class PointSystemPlugin(BirthdayFeatureMixin, LotteryFeatureMixin, Star):
    def __init__(self, context: Context, config: Dict[str, Any]):
        super().__init__(context)
        self.config = config
        self._data_lock = asyncio.Lock()
        self._backup_task: asyncio.Task | None = None
        self._backup_stop_event = asyncio.Event()
        self._birthday_broadcast_task: asyncio.Task | None = None
        self._birthday_broadcast_stop_event = asyncio.Event()
        # 头衔同步属于可选展示能力；权限不足时暂缓重试，避免每条消息重复调用失败接口。
        self._negative_title_retry_after: dict[str, datetime.datetime] = {}
        self.page_api = None

        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.data_file = os.path.join(self.data_dir, "points_data.json")
        os.makedirs(self.data_dir, exist_ok=True)

        self.data, migrated = self._load_data_sync()
        snapshot_created = self._record_point_snapshot()
        if migrated or snapshot_created:
            self._write_data_sync()
        self._register_page_api()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self._backup_task = loop.create_task(self._auto_backup_loop())
            self._birthday_broadcast_task = loop.create_task(
                self._birthday_broadcast_loop()
            )

    def _register_page_api(self) -> None:
        if not callable(getattr(self.context, "register_web_api", None)):
            logger.warning("[PointSystem] 当前 AstrBot 不支持插件拓展页 API")
            return
        try:
            from .page_api import PointSystemPageApi

            self.page_api = PointSystemPageApi(self)
            self.page_api.register_routes()
        except Exception as exc:
            self.page_api = None
            logger.warning(f"[PointSystem] 拓展页 API 注册失败: {exc}")

    def _new_store(self) -> Dict[str, Any]:
        return {
            "version": DATA_VERSION,
            "users": {},
            "groups": {},
            "exchange_redemptions": [],
            "private_message_targets": {},
            "red_packets": [],
            "point_snapshots": [],
            "reset_generation": 0,
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

    def _normalize_command_text(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).replace("\u3000", " ")
        text = text.replace("\r", " ").replace("\n", " ")
        return re.sub(r"\s+", " ", text).strip()

    def _strip_command_prefix(self, text: str) -> str:
        normalized = self._normalize_command_text(text)
        while normalized.startswith(COMMAND_PREFIXES):
            normalized = normalized[1:].lstrip()
        return normalized

    def _split_command_text(self, value: Any) -> tuple[str, str]:
        command_text = self._strip_command_prefix(str(value) if value is not None else "")
        command_text = MESSAGE_ID_SUFFIX_PATTERN.sub("", command_text).rstrip()
        if not command_text:
            return "", ""

        for command_name in REGISTERED_COMMAND_NAMES_BY_LENGTH:
            if command_text == command_name:
                return command_name, ""
            if command_text.startswith(command_name):
                next_char = command_text[len(command_name) : len(command_name) + 1]
                if next_char and next_char.isspace():
                    return command_name, command_text[len(command_name) :].strip()

        head, _, tail = command_text.partition(" ")
        return head.strip(), tail.strip()

    def _normalize_user_id(self, value: Any) -> str:
        return str(value).strip()

    def _single_line_message(self, text: Any) -> str:
        if text is None:
            return ""
        normalized = str(text).replace("\r", " ").replace("\n", " ").strip()
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = normalized.replace("；", "，").replace(";", "，")
        parts = [
            part.strip(" ，。！？!?")
            for part in re.split(r"[。！？!?]+", normalized)
            if part.strip(" ，。！？!?")
        ]
        if not parts:
            return ""
        return "，".join(parts) + "。"

    def _freeform_reply_message(self, text: Any) -> str:
        if text is None:
            return ""
        normalized = str(text).replace("\r", "\n").strip()
        if not normalized:
            return ""

        normalized = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", normalized)
        normalized = re.sub(r"`([^`]*)`", r"\1", normalized)
        normalized = re.sub(r"\*\*([^*]+)\*\*", r"\1", normalized)
        normalized = re.sub(r"\*([^*]+)\*", r"\1", normalized)
        normalized = re.sub(r"__([^_]+)__", r"\1", normalized)
        normalized = re.sub(r"_([^_]+)_", r"\1", normalized)
        normalized = normalized.replace("\n", " ")
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = re.sub(r"\s+([，。！？!?~～…])", r"\1", normalized).strip()
        normalized = re.sub(r"([。！？!?])\1+", r"\1", normalized)
        normalized = re.sub(r"(~|～){3,}", r"\1\1", normalized)
        if not normalized:
            return ""
        if normalized[-1] not in "。！？!?~～…":
            normalized += "。"
        return normalized

    def _pick_random_reply_variant(
        self, options: list[str], default: str = ""
    ) -> str:
        candidates = [self._normalize_text(item).strip() for item in options]
        candidates = [item for item in candidates if item]
        if not candidates:
            return default
        return random.choice(candidates)

    def _build_sign_in_fortune_fallback(
        self, is_lucky: bool, points_delta: int, points_name: str
    ) -> str:
        if is_lucky:
            return self._pick_random_reply_variant(
                [
                    f"这波也太欧了，白捡 {points_delta} {points_name} 属实离谱。",
                    f"今天这手气像开挂了一样，顺手就多摸了 {points_delta} {points_name}。",
                    f"谁把欧气全塞你这边了，一下就赚了 {points_delta} {points_name}。",
                    f"这一下直接血赚 {points_delta} {points_name}，看得人都想蹭点运气。",
                ],
                default=f"今天这手气有点离谱，白捡了 {points_delta} {points_name}。",
            )
        return self._pick_random_reply_variant(
            [
                f"好消息是签到了，坏消息是欧气今天没上线，先掉了 {points_delta} {points_name}。",
                f"今天像被命运顺手薅了一把，一下掉了 {points_delta} {points_name}。",
                f"这波有点惨，刚签到就先交出去 {points_delta} {points_name}。",
                f"手气像是睡过头了，被扣这 {points_delta} {points_name} 多少有点肉疼。",
            ],
            default=f"今天被命运轻轻绊了一下，掉了 {points_delta} {points_name}。",
        )

    def _plain_result(self, event: AstrMessageEvent, text: Any):
        return event.plain_result(self._single_line_message(text))

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

    def _normalize_delivery_contents(self, value: Any) -> list[str]:
        if isinstance(value, str):
            raw_items = value.splitlines()
        elif isinstance(value, list):
            raw_items = [str(item) for item in value]
        else:
            raw_items = []

        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            content = str(item).strip()
            if content and content not in seen:
                seen.add(content)
                result.append(content)
        return result

    @staticmethod
    def _exchange_content_media(content: Any) -> tuple[str, str]:
        """Return (kind, source) for the optional media shorthand.

        Existing plain text inventory remains unchanged.  Media inventory uses
        ``image:URL`` or ``video:URL`` so the official list-style config stays
        backwards compatible.
        """
        raw = str(content or "").strip()
        match = re.match(r"^(image|video)\s*(?:://|:|\|)\s*(.+)$", raw, re.IGNORECASE)
        if not match:
            return "", ""
        source = match.group(2).strip()
        return (match.group(1).casefold(), source) if source else ("", "")

    @classmethod
    def _exchange_content_type(cls, value: Any, contents: Any = None) -> str:
        normalized = str(value or "").strip().casefold()
        if normalized in {"text", "image", "video"}:
            return normalized
        detected = {
            cls._exchange_content_media(content)[0]
            for content in (contents if isinstance(contents, list) else [])
        }
        detected.discard("")
        return next(iter(detected)) if len(detected) == 1 else "text"

    @classmethod
    def _exchange_content_display(cls, content: Any) -> str:
        kind, _source = cls._exchange_content_media(content)
        return {"image": "[图片]", "video": "[视频]"}.get(kind, str(content or ""))

    @classmethod
    def _exchange_content_components(
        cls, message: str, content: Any
    ) -> list[Any]:
        components: list[Any] = [Plain(message)]
        kind, source = cls._exchange_content_media(content)
        if not source:
            return components
        if kind == "image":
            if source.startswith(("http://", "https://")):
                components.append(Image(file=None, url=source))
            else:
                components.append(Image(file=source))
        elif kind == "video":
            components.append(Video(file=source))
        return components

    def _exchange_content_fingerprint(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _private_send_result_status(result: Any, route: str) -> str:
        if route == "recorded_private_session":
            if result is True:
                return PRIVATE_SEND_SUCCESS
            if result is False:
                return PRIVATE_SEND_FAILED
            return PRIVATE_SEND_UNCERTAIN

        if not isinstance(result, dict):
            return PRIVATE_SEND_UNCERTAIN
        status = str(result.get("status", "")).strip().casefold()
        if status and status not in {"ok", "success"}:
            return PRIVATE_SEND_FAILED
        retcode = result.get("retcode")
        if retcode is not None:
            try:
                return (
                    PRIVATE_SEND_SUCCESS
                    if int(retcode) == 0
                    else PRIVATE_SEND_FAILED
                )
            except (TypeError, ValueError):
                return PRIVATE_SEND_FAILED
        if status in {"ok", "success"} or result.get("message_id") is not None:
            return PRIVATE_SEND_SUCCESS
        return PRIVATE_SEND_UNCERTAIN

    @staticmethod
    def _event_is_private_chat(event: AstrMessageEvent) -> bool:
        private_check = getattr(event, "is_private_chat", None)
        if callable(private_check):
            try:
                return bool(private_check())
            except Exception:
                pass

        session = getattr(event, "session", None)
        if getattr(session, "message_type", None) == MessageType.FRIEND_MESSAGE:
            return True
        message_type_getter = getattr(event, "get_message_type", None)
        if callable(message_type_getter):
            try:
                return message_type_getter() == MessageType.FRIEND_MESSAGE
            except Exception:
                pass
        return False

    def _event_platform_id(self, event: AstrMessageEvent) -> str:
        session = getattr(event, "session", None)
        return self._normalize_text(
            getattr(session, "platform_id", "")
            or getattr(session, "platform_name", "")
            or getattr(getattr(event, "platform_meta", None), "id", "")
        ).strip()

    def _private_message_target_key(
        self, event: AstrMessageEvent, user_id: str
    ) -> str:
        platform_id = self._event_platform_id(event)
        normalized_user_id = self._normalize_user_id(user_id)
        if not platform_id or not normalized_user_id:
            return ""
        return f"{platform_id}|{normalized_user_id}"

    def _normalize_private_message_targets(self, raw: Any) -> Dict[str, str]:
        if not isinstance(raw, dict):
            return {}

        targets: Dict[str, str] = {}
        for raw_key, raw_target in raw.items():
            key = self._normalize_text(raw_key).strip()[:300]
            target = self._normalize_text(raw_target).strip()[:600]
            if not key or not target:
                continue
            try:
                session = MessageSession.from_str(target)
            except (TypeError, ValueError):
                continue
            if session.message_type == MessageType.FRIEND_MESSAGE:
                targets[key] = str(session)
        return targets

    def _remember_private_message_target_locked(
        self, event: AstrMessageEvent, user_id: str
    ) -> bool:
        if not self._event_is_private_chat(event):
            return False

        key = self._private_message_target_key(event, user_id)
        raw_session = getattr(event, "session", None)
        target = self._normalize_text(
            str(raw_session) if raw_session is not None else ""
        ).strip()
        if not target:
            target = self._normalize_text(
                getattr(event, "unified_msg_origin", "")
            ).strip()
        if not key or not target:
            return False
        try:
            session = MessageSession.from_str(target)
        except (TypeError, ValueError):
            return False
        if session.message_type != MessageType.FRIEND_MESSAGE:
            return False

        targets = self.data.setdefault("private_message_targets", {})
        normalized_target = str(session)
        if targets.get(key) == normalized_target:
            return False
        targets[key] = normalized_target
        return True

    def _get_recorded_private_message_target(
        self, event: AstrMessageEvent, user_id: str
    ) -> MessageSession | None:
        key = self._private_message_target_key(event, user_id)
        raw_target = self.data.get("private_message_targets", {}).get(key)
        if not key or not raw_target:
            return None
        try:
            session = MessageSession.from_str(str(raw_target))
        except (TypeError, ValueError):
            return None
        if (
            session.message_type != MessageType.FRIEND_MESSAGE
            or session.platform_id != self._event_platform_id(event)
        ):
            return None
        return session

    async def _send_private_text(
        self, event: AstrMessageEvent, user_id: str, message: str
    ) -> str:
        return await self._send_private_exchange(event, user_id, message)

    async def _send_private_exchange(
        self,
        event: AstrMessageEvent,
        user_id: str,
        message: str,
        content: str = "",
    ) -> str:
        """Send text or one optional media component over one trusted route."""
        normalized_user_id = self._normalize_user_id(user_id)
        if not normalized_user_id:
            return PRIVATE_SEND_FAILED

        components = self._exchange_content_components(message, content)
        has_media = len(components) > 1

        target_user_id: int | str = (
            int(normalized_user_id)
            if normalized_user_id.isdigit()
            else normalized_user_id
        )
        route = ""
        sender = None
        sender_args: tuple[Any, ...] = ()
        sender_kwargs: Dict[str, Any] = {}
        bot = getattr(event, "bot", None)
        private_session = self._get_recorded_private_message_target(
            event, normalized_user_id
        )
        context_sender = getattr(self.context, "send_message", None)

        # AstrBot's normal session sender converts local Image components to
        # Base64 before handing them to OneBot.  Prefer it for media so local
        # uploads work in private chats as well as text rewards.
        if has_media and private_session is not None and callable(context_sender):
            route = "recorded_private_session"
            sender = context_sender
            sender_args = (private_session, MessageChain(components))

        platform_meta_name = self._normalize_text(
            getattr(getattr(event, "platform_meta", None), "name", "")
        ).strip().casefold()
        is_aiocqhttp_event = isinstance(event, AiocqhttpMessageEvent) or (
            platform_meta_name == "aiocqhttp"
        )
        if sender is None and is_aiocqhttp_event and bot is not None:
            action_sender = getattr(getattr(bot, "api", None), "call_action", None)
            direct_sender = getattr(bot, "send_private_msg", None)
            if callable(action_sender):
                route = "onebot_call_action"
                sender = action_sender
                sender_args = ("send_private_msg",)
                sender_kwargs = {
                    "user_id": target_user_id,
                    "message": (
                        [component.toDict() for component in components]
                        if has_media
                        else message
                    ),
                }
            elif callable(direct_sender):
                route = "onebot_send_private_msg"
                sender = direct_sender
                sender_kwargs = {
                    "user_id": target_user_id,
                    "message": (
                        [component.toDict() for component in components]
                        if has_media
                        else message
                    ),
                }

        if sender is None:
            if private_session is not None and callable(context_sender):
                route = "recorded_private_session"
                sender = context_sender
                sender_args = (private_session, MessageChain(components))

        if sender is None:
            logger.warning(
                f"[PointSystem] 无可用私聊发送路由: user={normalized_user_id}, "
                f"platform={self._event_platform_id(event) or 'unknown'}"
            )
            return PRIVATE_SEND_FAILED

        try:
            result = sender(*sender_args, **sender_kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.warning(
                f"[PointSystem] 兑换私聊发送状态不确定: user={normalized_user_id}, "
                f"route={route}, error_type={type(exc).__name__}"
            )
            return PRIVATE_SEND_UNCERTAIN

        status = self._private_send_result_status(result, route)
        if status == PRIVATE_SEND_FAILED:
            logger.warning(
                f"[PointSystem] 兑换私聊发送被平台明确拒绝: "
                f"user={normalized_user_id}, route={route}"
            )
        return status

    async def _mark_exchange_delivery_status(
        self, redemption: Dict[str, Any], status: str
    ) -> bool:
        async with self._data_lock:
            redemptions = self.data.setdefault("exchange_redemptions", [])
            redemption_id = self._normalize_text(
                redemption.get("redemption_id")
            ).strip()
            current = next(
                (
                    item
                    for item in redemptions
                    if isinstance(item, dict)
                    and item.get("redemption_id") == redemption_id
                ),
                None,
            )
            if current is None:
                return False
            current["delivery_status"] = (
                "delivered" if status == PRIVATE_SEND_SUCCESS else "uncertain"
            )
            if status == PRIVATE_SEND_SUCCESS:
                current["delivered_at"] = datetime.datetime.now().isoformat(
                    timespec="seconds"
                )
            return await self._save_data_locked()

    async def _rollback_failed_private_exchange(
        self,
        sender_id: str,
        cost: int,
        redemption: Dict[str, Any],
    ) -> bool:
        """私聊失败时退还积分并释放库存，保存失败则恢复原兑换状态。"""
        async with self._data_lock:
            redemptions = self.data.setdefault("exchange_redemptions", [])
            redemption_id = self._normalize_text(
                redemption.get("redemption_id")
            ).strip()
            redemption_index = next(
                (
                    index
                    for index, current in enumerate(redemptions)
                    if isinstance(current, dict)
                    and current.get("redemption_id") == redemption_id
                ),
                None,
            )
            if redemption_index is None:
                logger.error(
                    f"[PointSystem] 私聊失败后未找到待回滚兑换记录: user={sender_id}"
                )
                return False

            current_redemption = redemptions[redemption_index]
            current_generation = self._normalize_int(
                self.data.get("reset_generation"), 0, minimum=0
            )
            redemption_generation = self._normalize_int(
                current_redemption.get("reset_generation"), 0, minimum=0
            )
            if redemption_generation != current_generation:
                current_redemption["delivery_status"] = "uncertain"
                await self._save_data_locked()
                logger.warning(
                    f"[PointSystem] 数据重置后跳过旧兑换退款: redemption={redemption_id}"
                )
                return False

            users = self.data.setdefault("users", {})
            if sender_id not in users:
                current_redemption["delivery_status"] = "uncertain"
                await self._save_data_locked()
                return False

            removed_redemption = redemptions.pop(redemption_index)
            user_info = users[sender_id]
            user_info["points"] += cost
            if await self._save_data_locked():
                return True

            user_info["points"] -= cost
            redemptions.insert(redemption_index, removed_redemption)
            logger.error(
                f"[PointSystem] 私聊失败后的兑换回滚保存失败: user={sender_id}"
            )
            return False

    def _normalize_exchange_redemptions(self, raw: Any) -> list[Dict[str, Any]]:
        if not isinstance(raw, list):
            return []

        redemptions: list[Dict[str, Any]] = []
        seen_hashes: set[str] = set()
        repeatable_items = {
            self._normalize_command_text(item.get("name")).casefold()
            for item in self._get_exchange_items()
            if self._exchange_item_repeatable(item)
        }
        for item in raw:
            if not isinstance(item, dict):
                continue
            content_hash = self._normalize_text(item.get("content_hash")).strip().lower()
            item_name = self._normalize_text(item.get("item_name")).strip()
            repeatable = bool(item.get("repeatable")) or (
                item_name.casefold() in repeatable_items
            )
            if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
                continue
            if content_hash in seen_hashes and not repeatable:
                continue
            if not repeatable:
                seen_hashes.add(content_hash)
            redemptions.append(
                {
                    "redemption_id": self._normalize_text(
                        item.get("redemption_id")
                    ).strip()
                    or hashlib.sha256(
                        (
                            f"{content_hash}|{item.get('user_id', '')}|"
                            f"{item.get('redeemed_at', '')}"
                        ).encode("utf-8")
                    ).hexdigest()[:32],
                    "content_hash": content_hash,
                    "item_name": item_name,
                    "user_id": self._normalize_user_id(item.get("user_id", "")),
                    "group_id": self._normalize_user_id(item.get("group_id", "")),
                    "redeemed_at": self._normalize_text(item.get("redeemed_at")),
                    "cost": self._normalize_int(item.get("cost"), 0, minimum=0),
                    "repeatable": repeatable,
                    "delivery_status": (
                        "uncertain"
                        if self._normalize_text(item.get("delivery_status"))
                        .strip()
                        .casefold()
                        in {"pending", "uncertain"}
                        else "delivered"
                    ),
                    "delivery_channel": self._normalize_text(
                        item.get("delivery_channel", "legacy")
                    ).strip()[:32]
                    or "legacy",
                    "delivered_at": self._normalize_text(
                        item.get("delivered_at")
                    ).strip(),
                    "reset_generation": self._normalize_int(
                        item.get("reset_generation"), 0, minimum=0
                    ),
                }
            )
        return redemptions

    def _normalize_red_packets(self, raw: Any) -> list[Dict[str, Any]]:
        if not isinstance(raw, list):
            return []

        packets: list[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue

            packet_id = self._normalize_text(item.get("packet_id")).strip().casefold()
            packet_type = self._normalize_text(item.get("packet_type")).strip().casefold()
            if (
                not re.fullmatch(r"[a-z0-9]{6,32}", packet_id)
                or packet_id in seen_ids
                or packet_type not in {"fixed", "lucky", "password"}
            ):
                continue

            total_points = self._normalize_int(
                item.get("total_points"), 0, minimum=1
            )
            total_count = self._normalize_int(item.get("total_count"), 0, minimum=1)
            if total_points <= 0 or total_count <= 0:
                continue

            claimed_user_ids: list[str] = []
            seen_users: set[str] = set()
            raw_claimed_users = item.get("claimed_user_ids", [])
            if isinstance(raw_claimed_users, list):
                for raw_user_id in raw_claimed_users:
                    user_id = self._normalize_user_id(raw_user_id)
                    if user_id and user_id not in seen_users:
                        seen_users.add(user_id)
                        claimed_user_ids.append(user_id)
                    if len(claimed_user_ids) >= total_count:
                        break

            claimed_records: list[Dict[str, Any]] = []
            raw_claimed_records = item.get("claimed_records", [])
            if isinstance(raw_claimed_records, list):
                for raw_record in raw_claimed_records:
                    if not isinstance(raw_record, dict):
                        continue
                    record_user_id = self._normalize_user_id(raw_record.get("user_id"))
                    record_amount = self._normalize_int(
                        raw_record.get("amount"), 0, minimum=1
                    )
                    if not record_user_id or record_amount <= 0:
                        continue
                    claimed_records.append(
                        {
                            "user_id": record_user_id,
                            "display_name": self._normalize_text(
                                raw_record.get("display_name")
                            ).strip(),
                            "amount": record_amount,
                        }
                    )
                    if len(claimed_records) >= total_count:
                        break

            remaining_count = min(
                max(
                    self._normalize_int(
                        item.get("remaining_count"),
                        total_count - len(claimed_user_ids),
                        minimum=0,
                    ),
                    total_count,
                ),
                total_count - len(claimed_user_ids),
            )
            remaining_points = min(
                max(
                    self._normalize_int(
                        item.get("remaining_points"), total_points, minimum=0
                    ),
                    0,
                ),
                total_points,
            )
            password_hash = self._normalize_text(item.get("password_hash")).strip().lower()
            if packet_type == "password" and not re.fullmatch(r"[0-9a-f]{64}", password_hash):
                continue

            seen_ids.add(packet_id)
            packets.append(
                {
                    "packet_id": packet_id,
                    "packet_type": packet_type,
                    "total_points": total_points,
                    "remaining_points": remaining_points,
                    "total_count": total_count,
                    "remaining_count": remaining_count,
                    "unit_points": self._normalize_int(
                        item.get("unit_points"),
                        total_points // total_count,
                        minimum=1,
                    ),
                    "claimed_user_ids": claimed_user_ids,
                    "claimed_records": claimed_records,
                    "group_id": self._normalize_user_id(item.get("group_id", "")),
                    "sender_id": self._normalize_user_id(item.get("sender_id", "")),
                    "password_hash": password_hash if packet_type == "password" else "",
                    "created_at": self._normalize_text(item.get("created_at")),
                    "expires_at": self._normalize_text(item.get("expires_at")),
                    "reset_generation": self._normalize_int(
                        item.get("reset_generation"), 0, minimum=0
                    ),
                }
            )
        return packets

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
            "make_up_sign_in_month": self._normalize_text(
                raw.get("make_up_sign_in_month")
            ),
            "make_up_sign_in_count": self._normalize_int(
                raw.get("make_up_sign_in_count"), 0, 0
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
            "steal_points_date": self._normalize_text(raw.get("steal_points_date")),
            "daily_steal_points_times": self._normalize_int(
                raw.get("daily_steal_points_times"), 0, 0
            ),
            "stolen_points_date": self._normalize_text(raw.get("stolen_points_date")),
            "daily_stolen_points_times": self._normalize_int(
                raw.get("daily_stolen_points_times"), 0, 0
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

    def _normalize_point_snapshots(
        self, raw: Any, now: datetime.datetime | None = None
    ) -> list[Dict[str, Any]]:
        if not isinstance(raw, list):
            return []

        current = now or datetime.datetime.now()
        cutoff = current - datetime.timedelta(days=POINT_SNAPSHOT_RETENTION_DAYS)
        by_bucket: Dict[datetime.datetime, Dict[str, Any]] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            captured_text = self._normalize_text(item.get("captured_at")).strip()
            try:
                captured_at = datetime.datetime.fromisoformat(captured_text)
            except (TypeError, ValueError):
                continue
            if captured_at.tzinfo is not None:
                captured_at = captured_at.astimezone().replace(tzinfo=None)
            if captured_at < cutoff or captured_at > current + datetime.timedelta(days=1):
                continue

            raw_groups = item.get("groups", {})
            snapshot_groups: Dict[str, Any] = {}
            if isinstance(raw_groups, dict):
                for raw_group_id, raw_group in raw_groups.items():
                    group_id = self._normalize_user_id(raw_group_id)
                    if not group_id or not isinstance(raw_group, dict):
                        continue
                    snapshot_groups[group_id] = {
                        "total_points": self._normalize_signed_int(
                            raw_group.get("total_points"), 0
                        ),
                        "user_count": self._normalize_int(
                            raw_group.get("user_count"), 0, minimum=0
                        ),
                    }

            normalized = {
                "captured_at": captured_at.isoformat(timespec="seconds"),
                "total_points": self._normalize_signed_int(
                    item.get("total_points"), 0
                ),
                "user_count": self._normalize_int(
                    item.get("user_count"), 0, minimum=0
                ),
                "groups": snapshot_groups,
            }
            bucket = captured_at.replace(
                minute=(captured_at.minute // POINT_SNAPSHOT_BUCKET_MINUTES)
                * POINT_SNAPSHOT_BUCKET_MINUTES,
                second=0,
                microsecond=0,
            )
            previous = by_bucket.get(bucket)
            if previous is None or normalized["captured_at"] >= previous["captured_at"]:
                by_bucket[bucket] = normalized

        snapshots = [by_bucket[key] for key in sorted(by_bucket)]
        return snapshots[-POINT_SNAPSHOT_MAX_RECORDS:]

    def _record_point_snapshot(
        self, now: datetime.datetime | None = None
    ) -> bool:
        captured_at = (now or datetime.datetime.now()).replace(microsecond=0)
        users = self.data.get("users", {})
        groups = self.data.get("groups", {})
        if not isinstance(users, dict):
            users = {}
        if not isinstance(groups, dict):
            groups = {}

        normalized_users = {
            str(user_id): record
            for user_id, record in users.items()
            if isinstance(record, dict)
        }
        balances = {
            user_id: self._normalize_signed_int(record.get("points"), 0)
            for user_id, record in normalized_users.items()
        }
        snapshot_groups: Dict[str, Any] = {}
        for raw_group_id, raw_group in groups.items():
            group_id = self._normalize_user_id(raw_group_id)
            if not group_id or not isinstance(raw_group, dict):
                continue
            members = raw_group.get("members", {})
            member_ids = (
                {str(user_id) for user_id in members}
                if isinstance(members, dict)
                else set()
            )
            member_balances = [
                balances[user_id]
                for user_id in member_ids
                if user_id in balances
            ]
            snapshot_groups[group_id] = {
                "total_points": sum(member_balances),
                "user_count": len(member_balances),
            }

        snapshot = {
            "captured_at": captured_at.isoformat(timespec="seconds"),
            "total_points": sum(balances.values()),
            "user_count": len(normalized_users),
            "groups": snapshot_groups,
        }
        previous = self.data.get("point_snapshots", [])
        normalized = self._normalize_point_snapshots(
            [*(previous if isinstance(previous, list) else []), snapshot],
            now=captured_at,
        )
        changed = normalized != previous
        self.data["point_snapshots"] = normalized
        return changed

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

            normalized_users: Dict[str, Dict[str, Any]] = {}
            for user_id, user_info in raw_users.items():
                normalized_user_id = self._normalize_user_id(user_id)
                if not normalized_user_id:
                    migrated = True
                    continue
                if normalized_user_id != str(user_id):
                    migrated = True
                if normalized_user_id in normalized_users:
                    logger.warning(
                        f"检测到重复的用户 ID 键，已按归一化结果覆盖旧值: {user_id!r} -> {normalized_user_id!r}"
                    )
                    migrated = True
                normalized_users[normalized_user_id] = self._normalize_user_record(
                    user_info
                )
            groups = self._normalize_group_store(raw.get("groups", {}), normalized_users)

            if raw.get("version") != DATA_VERSION:
                migrated = True

            store["users"] = normalized_users
            store["groups"] = groups
            store["exchange_redemptions"] = self._normalize_exchange_redemptions(
                raw.get("exchange_redemptions", [])
            )
            store["private_message_targets"] = self._normalize_private_message_targets(
                raw.get("private_message_targets", {})
            )
            store["red_packets"] = self._normalize_red_packets(
                raw.get("red_packets", [])
            )
            store["point_snapshots"] = self._normalize_point_snapshots(
                raw.get("point_snapshots", [])
            )
            store["reset_generation"] = self._normalize_int(
                raw.get("reset_generation"), 0, minimum=0
            )
            return store, migrated

        # 兼容旧版扁平结构：{user_id: user_record}
        legacy_users: Dict[str, Dict[str, Any]] = {}
        for user_id, user_info in raw.items():
            normalized_user_id = self._normalize_user_id(user_id)
            if not isinstance(user_info, dict) or not normalized_user_id:
                continue
            legacy_users[normalized_user_id] = self._normalize_user_record(user_info)
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

    async def _save_data_locked(self) -> bool:
        previous_snapshots = self.data.get("point_snapshots")
        had_snapshots = "point_snapshots" in self.data
        self._record_point_snapshot()
        try:
            await asyncio.to_thread(self._write_data_sync)
            return True
        except Exception as exc:
            if had_snapshots:
                self.data["point_snapshots"] = previous_snapshots
            else:
                self.data.pop("point_snapshots", None)
            logger.error(f"保存积分数据失败: {exc}")
            return False

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
            "make_up_cost": self._normalize_int(
                sign_in_cfg.get("make_up_cost"), 100, minimum=0
            ),
            "make_up_monthly_limit": self._normalize_int(
                sign_in_cfg.get("make_up_monthly_limit"), 1, minimum=0
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

    def _append_unique_trigger(
        self, variants: list[str], candidate: str
    ) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    def _append_trigger_keyword_variants(
        self, variants: list[str], value: Any, action_word: str
    ) -> None:
        normalized = self._normalize_trigger_token(value)
        if not normalized:
            return

        extracted = self._extract_trigger_keyword(normalized, action_word)
        if normalized == action_word:
            self._append_unique_trigger(variants, action_word)
            return

        if extracted and extracted != normalized:
            self._append_unique_trigger(variants, normalized)
            self._append_unique_trigger(variants, f"{extracted}{action_word}")
            self._append_unique_trigger(variants, f"{action_word}{extracted}")
            return

        self._append_unique_trigger(variants, f"{normalized}{action_word}")
        self._append_unique_trigger(variants, f"{action_word}{normalized}")

    def _append_full_trigger_variants(
        self, variants: list[str], value: Any, action_word: str
    ) -> None:
        normalized = self._normalize_trigger_token(value)
        if not normalized:
            return

        self._append_unique_trigger(variants, normalized)
        extracted = self._extract_trigger_keyword(normalized, action_word)
        if extracted and extracted != normalized:
            self._append_unique_trigger(variants, f"{extracted}{action_word}")
            self._append_unique_trigger(variants, f"{action_word}{extracted}")

    def _get_action_trigger_variants(self, action_word: str) -> list[str]:
        keyword = (
            self._get_sign_in_trigger_keyword()
            if action_word in {"签到", "补签"}
            else self._get_lottery_trigger_keyword()
        )
        variants: list[str] = []
        self._append_trigger_keyword_variants(variants, keyword, action_word)

        if action_word == "签到":
            self._append_full_trigger_variants(
                variants, self.config.get("sign_in_trigger", ""), action_word
            )
        elif action_word == "抽奖":
            self._append_full_trigger_variants(
                variants, self.config.get("lottery_trigger", ""), action_word
            )
        return variants

    def _get_sign_in_triggers(self) -> list[str]:
        return self._get_action_trigger_variants("签到")

    def _get_make_up_sign_in_triggers(self) -> list[str]:
        variants = ["补签"]
        self._append_trigger_keyword_variants(
            variants, self._get_sign_in_trigger_keyword(), "补签"
        )
        legacy_keyword = self._extract_trigger_keyword(
            self.config.get("sign_in_trigger", ""), "签到"
        )
        if legacy_keyword:
            self._append_trigger_keyword_variants(
                variants, legacy_keyword, "补签"
            )
        return variants

    def _get_lottery_triggers(self) -> list[str]:
        return self._get_action_trigger_variants("抽奖")

    def _iter_quick_action_message_candidates(
        self, event: AstrMessageEvent, message: str | None = None
    ) -> list[str]:
        candidates: list[str] = []
        for candidate in (
            message,
            self._get_event_plain_text(event),
            getattr(getattr(event, "message_obj", None), "message_str", ""),
            getattr(event, "message_str", ""),
        ):
            normalized = self._normalize_trigger_token(candidate)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    def _match_quick_action_from_event(
        self, event: AstrMessageEvent, message: str | None = None
    ) -> str | None:
        candidates = self._iter_quick_action_message_candidates(event, message)
        sign_in_triggers = self._get_sign_in_triggers()
        lottery_triggers = self._get_lottery_triggers()
        make_up_sign_in_triggers = self._get_make_up_sign_in_triggers()

        for candidate in candidates:
            if candidate in sign_in_triggers:
                return "sign_in"
            if candidate in lottery_triggers:
                return "lottery"
            if candidate in make_up_sign_in_triggers:
                return "make_up_sign_in"

        is_wake_command = bool(getattr(event, "is_at_or_wake_command", False))
        if is_wake_command:
            for candidate in candidates:
                if candidate == "签到":
                    return "sign_in"
                if candidate == "抽奖":
                    return "lottery"
        return None

    def _match_quick_action(self, message: str) -> str | None:
        normalized = self._normalize_trigger_token(message)
        if not normalized:
            return None
        if normalized in self._get_sign_in_triggers():
            return "sign_in"
        if normalized in self._get_lottery_triggers():
            return "lottery"
        if normalized in self._get_make_up_sign_in_triggers():
            return "make_up_sign_in"
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

    def _get_red_packet_settings(self) -> Dict[str, Any]:
        packet_cfg = self.config.get("red_packet_settings", {})
        if not isinstance(packet_cfg, dict):
            packet_cfg = {}

        return {
            "enabled": bool(packet_cfg.get("enabled", True)),
            "max_total_points": min(
                self._normalize_int(
                    packet_cfg.get("max_total_points"), 100000, minimum=1
                ),
                1_000_000_000,
            ),
            "max_count": min(
                self._normalize_int(packet_cfg.get("max_count"), 100, minimum=1),
                10000,
            ),
            "expire_minutes": min(
                self._normalize_int(
                    packet_cfg.get("expire_minutes"), 1440, minimum=0
                ),
                525600,
            ),
        }

    def _get_steal_settings(self) -> Dict[str, Any]:
        """读取偷积分配置，并将范围和概率归一化到可执行值。"""
        steal_cfg = self.config.get("steal_settings")
        if not isinstance(steal_cfg, dict):
            # 兼容可能采用的旧命名，便于从早期测试配置平滑迁移。
            steal_cfg = self.config.get("steal_points_settings", {})
        if not isinstance(steal_cfg, dict):
            steal_cfg = {}

        min_points = self._normalize_int(
            steal_cfg.get("min_points"), 1, minimum=1
        )
        max_points = self._normalize_int(
            steal_cfg.get("max_points"), 20, minimum=1
        )
        if max_points < min_points:
            min_points, max_points = max_points, min_points

        return {
            "enabled": bool(steal_cfg.get("enabled", False)),
            "daily_steal_limit": self._normalize_int(
                steal_cfg.get("daily_steal_limit"), 3, minimum=0
            ),
            "daily_be_stolen_limit": self._normalize_int(
                steal_cfg.get("daily_be_stolen_limit"), 3, minimum=0
            ),
            "failure_counts_as_stolen": bool(
                steal_cfg.get("failure_counts_as_stolen", False)
            ),
            "min_points": min_points,
            "max_points": max_points,
            "success_probability": min(
                self._normalize_float(
                    steal_cfg.get("success_probability"), 0.5, minimum=0.0
                ),
                1.0,
            ),
            "failure_cost": self._normalize_int(
                steal_cfg.get("failure_cost"), 0, minimum=0
            ),
            "failure_cost_to_victim": bool(
                steal_cfg.get("failure_cost_to_victim", True)
            ),
        }

    @staticmethod
    def _red_packet_type(raw_type: Any) -> str:
        normalized = str(raw_type or "").strip().casefold()
        if normalized in {"固定", "固定红包", "定额", "fixed"}:
            return "fixed"
        if normalized in {
            "拼手气",
            "拼手气红包",
            "随机",
            "随机红包",
            "lucky",
        }:
            return "lucky"
        if normalized in {"口令", "口令红包", "password"}:
            return "password"
        return ""

    @staticmethod
    def _red_packet_help() -> str:
        return (
            "用法：/积分红包 固定 每份积分 份数；"
            "/积分红包 拼手气 总积分 份数；"
            "/积分红包 口令 总积分 份数 口令。"
            "创建后，成员发送 /抢红包 编号，口令红包需追加口令。"
        )

    @staticmethod
    def _red_packet_password_hash(password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def _red_packet_expired(self, packet: Dict[str, Any]) -> bool:
        expires_at = self._normalize_text(packet.get("expires_at")).strip()
        if not expires_at:
            return False
        try:
            return datetime.datetime.now() >= datetime.datetime.fromisoformat(expires_at)
        except ValueError:
            return False

    def _red_packet_claim_amount(self, packet: Dict[str, Any]) -> int:
        remaining_points = self._normalize_int(
            packet.get("remaining_points"), 0, minimum=0
        )
        remaining_count = self._normalize_int(
            packet.get("remaining_count"), 0, minimum=0
        )
        if remaining_points <= 0 or remaining_count <= 0:
            return 0
        if remaining_points < remaining_count:
            return 0

        if packet.get("packet_type") == "fixed":
            return min(
                self._normalize_int(packet.get("unit_points"), 1, minimum=1),
                remaining_points,
            )
        if remaining_count == 1:
            return remaining_points

        # 先为剩余份数预留最低 1 积分，再围绕平均值随机分配。
        max_amount = max(1, (remaining_points // remaining_count) * 2)
        max_amount = min(max_amount, remaining_points - remaining_count + 1)
        return random.randint(1, max_amount)

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

    def _get_birthday_settings(self) -> Dict[str, Any]:
        birthday_cfg = self.config.get("birthday_settings", {})
        if not isinstance(birthday_cfg, dict):
            birthday_cfg = {}

        trigger = self._normalize_trigger_token(
            birthday_cfg.get("sign_in_trigger", "生日签到"),
            "生日签到",
        )
        return {
            "enabled": bool(birthday_cfg.get("enabled", True)),
            "sign_in_trigger": trigger,
            "reward_points": self._normalize_int(
                birthday_cfg.get("reward_points"),
                50,
                minimum=0,
            ),
            "auto_record_when_unset": bool(
                birthday_cfg.get("auto_record_when_unset", True)
            ),
            "auto_broadcast_enabled": bool(
                birthday_cfg.get("auto_broadcast_enabled", True)
            ),
            "auto_broadcast_time": self._normalize_backup_time(
                birthday_cfg.get("auto_broadcast_time", "08:00")
            ),
        }

    def _get_exchange_items(self) -> list[Dict[str, Any]]:
        raw_items = self.config.get("exchange_items", [])
        if not isinstance(raw_items, list):
            return []

        items: list[Dict[str, Any]] = []
        seen_names: set[str] = set()
        seen_contents: set[str] = set()
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            name = self._normalize_command_text(raw_item.get("name"))
            normalized_name = name.casefold()
            if not name or normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            contents = [
                content
                for content in self._normalize_delivery_contents(
                    raw_item.get("contents", [])
                )
                if content not in seen_contents
            ]
            seen_contents.update(contents)
            items.append(
                {
                    "name": name,
                    "enabled": bool(raw_item.get("enabled", True)),
                    "cost": self._normalize_int(raw_item.get("cost"), 100, minimum=1),
                    "contents": contents,
                    "content_type": self._exchange_content_type(
                        raw_item.get("content_type"), contents
                    ),
                    "selection_mode": self._exchange_selection_mode(raw_item.get("selection_mode")),
                    "repeatable": bool(raw_item.get("repeatable", False)),
                    "private_only": bool(raw_item.get("private_only", True)),
                    "success_template": self._normalize_text(
                        raw_item.get("success_template")
                    ).strip()
                    or (
                        "兑换成功！\n兑换物：{item}\n兑换内容：{content}\n"
                        "消耗 {cost} {points_name}，剩余 {remaining} {points_name}。"
                    ),
                }
            )
        return items

    @staticmethod
    def _exchange_selection_mode(value: Any) -> str:
        normalized = str(value or "").strip().casefold()
        return "random" if normalized in {"random", "rand", "随机"} else "sequential"

    def _exchange_item_repeatable(self, item: Dict[str, Any]) -> bool:
        return bool(item.get("repeatable", False))

    def _exchange_redemption_consumes_stock(
        self,
        redemption: Dict[str, Any],
        items_by_name: Dict[str, Dict[str, Any]] | None = None,
    ) -> bool:
        if items_by_name is None:
            items_by_name = {
                str(item.get("name", "")).casefold(): item
                for item in self._get_exchange_items()
            }
        item = items_by_name.get(
            self._normalize_command_text(redemption.get("item_name")).casefold()
        )
        if self._exchange_item_repeatable(item or {}):
            return False
        if "repeatable" in redemption:
            return not bool(redemption.get("repeatable"))
        return not self._exchange_item_repeatable(item or {})

    def _get_exchange_scope(self) -> Dict[str, Any]:
        """读取自定义兑换物的全局适用范围。"""
        raw_scope = self.config.get("exchange_scope", {})
        if not isinstance(raw_scope, dict):
            raw_scope = {}

        mode_value = self._normalize_command_text(raw_scope.get("mode")).casefold()
        mode = (
            "whitelist"
            if mode_value in {"whitelist", "white", "allow", "白名单", "允许"}
            else "blacklist"
        )
        raw_values = raw_scope.get("scope", raw_scope.get("group_ids", []))
        values: list[str] = []
        seen: set[str] = set()
        for value in self._normalize_string_list(raw_values):
            normalized = self._normalize_command_text(value).casefold()
            if normalized and normalized not in seen:
                seen.add(normalized)
                values.append(normalized)
        return {"mode": mode, "scope": values}

    def _is_exchange_scope_allowed(
        self, event: AstrMessageEvent, user_id: str | None = None
    ) -> bool:
        """判断当前群或账号是否可以使用自定义兑换物。"""
        scope_config = self._get_exchange_scope()
        scope = set(scope_config["scope"])
        if not scope:
            return scope_config["mode"] == "blacklist"

        current_user_id = self._normalize_user_id(
            user_id if user_id is not None else event.get_sender_id()
        )
        current_values = {
            current_user_id.casefold(),
            f"user:{current_user_id}".casefold(),
        }
        group_id = self._get_group_id(event)
        group_ids = [group_id] if group_id else []
        # 私聊事件没有群号时，沿用已记录的群关系，兼容用户直接在私聊中兑换。
        if not group_id and current_user_id:
            group_ids.extend(self._collect_user_group_ids(current_user_id))
        for current_group_id in group_ids:
            if current_group_id:
                current_values.add(current_group_id.casefold())
                current_values.add(f"group:{current_group_id}".casefold())

        matched = bool(current_values.intersection(scope))
        return matched if scope_config["mode"] == "whitelist" else not matched

    def _find_exchange_item(
        self, raw_name: str, items: list[Dict[str, Any]]
    ) -> tuple[Dict[str, Any] | None, bool]:
        query = self._normalize_command_text(raw_name).casefold()
        if not query:
            return None, False

        enabled_items = [item for item in items if item["enabled"]]
        for item in enabled_items:
            if item["name"].casefold() == query:
                return item, False

        matches = [item for item in enabled_items if query in item["name"].casefold()]
        if len(matches) == 1:
            return matches[0], False
        return None, len(matches) > 1

    @staticmethod
    def _format_exchange_success_message(
        template: str,
        item_name: str,
        content: str,
        cost: int,
        points_name: str,
        remaining: int,
    ) -> str:
        message = template
        if "{content}" not in message:
            message += "\n兑换内容：{content}"
        replacements = {
            "{item}": item_name,
            "{content}": content,
            "{cost}": str(cost),
            "{points_name}": points_name,
            "{remaining}": str(remaining),
        }
        return re.sub(
            r"\{(?:item|content|cost|points_name|remaining)\}",
            lambda match: replacements[match.group(0)],
            message,
        )

    def _get_negative_settings(self) -> Dict[str, Any]:
        negative_cfg = self.config.get("negative_settings", {})
        if not isinstance(negative_cfg, dict):
            negative_cfg = {}

        debt_message = self._normalize_text(
            negative_cfg.get("debt_message", DEFAULT_NEGATIVE_DEBT_MESSAGE)
        ).strip()
        return {
            "debt_message": debt_message or DEFAULT_NEGATIVE_DEBT_MESSAGE,
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
            if configured in {
                LEGACY_DEFAULT_TEMPLATES.get(key),
                PREVIOUS_DEFAULT_TEMPLATES.get(key),
            }:
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
        user_id = self._normalize_user_id(user_id)
        users = self.data.setdefault("users", {})
        if user_id not in users:
            users[user_id] = self._normalize_user_record({})
        return users[user_id]

    def _get_message_segments(self, event: AstrMessageEvent) -> list[Any]:
        return list(getattr(event.message_obj, "message", []) or [])

    def _get_event_plain_text(self, event: AstrMessageEvent) -> str:
        segments = self._get_message_segments(event)
        plain_parts: list[str] = []
        for segment in segments:
            if isinstance(segment, Plain):
                text = self._normalize_text(getattr(segment, "text", ""))
                if text:
                    plain_parts.append(text)

        plain_text = "".join(plain_parts).strip()
        if plain_text:
            return plain_text

        message_obj = getattr(event, "message_obj", None)
        message_obj_text = self._normalize_text(
            getattr(message_obj, "message_str", "")
        ).strip()
        if message_obj_text:
            return message_obj_text

        return self._normalize_text(getattr(event, "message_str", "")).strip()

    def _get_group_id(self, event: AstrMessageEvent) -> str:
        group_id = event.get_group_id()
        if group_id is None:
            return ""
        return self._normalize_user_id(group_id)

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
        user_id = self._normalize_user_id(user_id)
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
        user_id = self._normalize_user_id(user_id)
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
                    return self._normalize_user_id(target_uid)
        return None

    def _extract_reply_message_id(self, event: AstrMessageEvent) -> int | None:
        for component in self._get_message_segments(event):
            if isinstance(component, Reply):
                reply_id = getattr(component, "id", None)
                try:
                    return int(reply_id)
                except (TypeError, ValueError):
                    continue
        return None

    def _is_safe_special_reward_regex(self, pattern: str) -> bool:
        if (
            not pattern
            or len(pattern) > MAX_SPECIAL_REWARD_REGEX_LENGTH
            or any(token in pattern for token in ("(?", "\\1", "\\g<", "(", ")"))
        ):
            return False
        return True

    def _match_special_reward_keyword(self, keyword: str, message: str) -> bool:
        keyword = self._normalize_text(keyword).strip()
        if not keyword:
            return False

        if not keyword.startswith("re:"):
            return keyword in message

        pattern = keyword[3:].strip()
        if not self._is_safe_special_reward_regex(pattern):
            logger.warning(f"已跳过不安全的日期奖励正则关键词: {keyword!r}")
            return False

        try:
            return bool(
                re.search(pattern, message[:MAX_SPECIAL_REWARD_MESSAGE_LENGTH])
            )
        except re.error:
            logger.warning(f"日期奖励正则关键词格式异常，已忽略: {keyword!r}")
            return False

    def _get_command_args(self, event: AstrMessageEvent) -> str:
        _, args = self._split_command_text(event.message_str or "")
        return args

    def _get_command_name(self, event: AstrMessageEvent) -> str:
        command_name, _ = self._split_command_text(event.message_str or "")
        return command_name or "该命令"

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
            if self._match_special_reward_keyword(keyword, message):
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
        stripped = self._normalize_command_text(message)
        if not stripped:
            return True
        if stripped.startswith(COMMAND_PREFIXES):
            return True
        if self._match_quick_action(stripped):
            return True
        if stripped == self._normalize_command_text(
            self._get_birthday_settings()["sign_in_trigger"]
        ):
            return True

        command_name, _ = self._split_command_text(stripped)
        return command_name in REGISTERED_COMMAND_NAMES

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
        return self._get_negative_settings()["debt_message"]

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
        points_name = self._get_points_name()
        fallback = self._build_sign_in_fortune_fallback(
            is_lucky, points_delta, points_name
        )
        if not provider:
            return self._freeform_reply_message(fallback)

        style_hint = self._pick_random_reply_variant(
            [
                "偏像群友路过时顺手接一句，短一点。",
                "偏像熟人看到后的第一反应，带一点俏皮吐槽。",
                "偏像旁边人在起哄，轻松一点就好。",
                "偏像朋友随口补一句，别太完整，别太端着。",
            ],
            default="像熟人顺手接一句，别太端着。",
        )
        prompt = (
            "你在一个很熟的中文群里当群友，看见有人签到触发彩蛋后顺手接一句。"
            f"当事人是 {reply_name}，触发的是“{title}”，"
            f"这次{'白赚了' if is_lucky else '被扣了'} {points_delta} 点积分。"
            f"当前总积分 {total_points} 只是背景信息，除非特别自然，否则不要重复报数字。"
            "直接输出群里要发出去的那句话。"
            "像真人第一反应，允许只抓一个重点，不用把事情复述完整。"
            "可以带一点口语、语气词、轻微调侃或夸张，但别油腻，也别像客服或播报。"
            "避免“签到成功”“当前共有”“触发了事件”这类整齐句式。"
            f"{style_hint}"
            "不要使用 markdown，不要分点，不要加引号，不要解释规则。"
            "长度控制在 10 到 32 个字，最好一句，最多两句短句。"
        )

        try:
            llm_resp = await provider.text_chat(
                prompt=prompt,
                session_id=event.unified_msg_origin,
                persist=False,
            )
            content = self._extract_llm_response_text(llm_resp)
            if content:
                return self._freeform_reply_message(content)
        except Exception as exc:
            logger.warning(f"签到彩蛋 LLM 回复失败，已回退默认文案: {exc}")

        return self._freeform_reply_message(fallback)

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
        user_id = self._normalize_user_id(user_id)

        async with self._data_lock:
            group_ids = self._collect_user_group_ids(user_id)
            users = self.data.get("users", {})
            groups = self.data.get("groups", {})
            target_user_info = users.get(user_id, {})
            if (
                target_user_info.get("points", 0) >= 0
                and not any(
                    self._normalize_text(
                        groups.get(group_id, {})
                        .get("members", {})
                        .get(user_id, {})
                        .get("negative_title")
                    )
                    for group_id in group_ids
                )
            ):
                return
            planned_updates: list[tuple[str, str, str]] = []

            for group_id in group_ids:
                retry_after = getattr(self, "_negative_title_retry_after", {}).get(
                    str(group_id)
                )
                if retry_after and datetime.datetime.now() < retry_after:
                    continue
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
        failed_groups: set[str] = set()
        for group_id, member_user_id, desired_title in planned_updates:
            if group_id in failed_groups:
                continue
            try:
                await event.bot.set_group_special_title(
                    group_id=int(group_id),
                    user_id=int(member_user_id),
                    special_title=desired_title,
                    duration=-1,
                )
                successful_updates.append((group_id, member_user_id, desired_title))
            except Exception as exc:
                retry_after_map = getattr(self, "_negative_title_retry_after", None)
                if retry_after_map is None:
                    retry_after_map = {}
                    self._negative_title_retry_after = retry_after_map
                retry_after_map[group_id] = datetime.datetime.now() + datetime.timedelta(
                    seconds=NEGATIVE_TITLE_RETRY_SECONDS
                )
                failed_groups.add(group_id)
                logger.warning(
                    f"同步负分头衔失败，已暂停该群重试 {NEGATIVE_TITLE_RETRY_SECONDS // 3600} 小时；"
                    f"不影响签到和积分恢复: group={group_id}, user={member_user_id}, "
                    f"title={desired_title!r}, error={exc}"
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
            retry_after_map = getattr(self, "_negative_title_retry_after", None)
            if retry_after_map is not None:
                for group_id, _, _ in successful_updates:
                    retry_after_map.pop(group_id, None)
            await self._save_data_locked()

    async def _clear_negative_titles_before_reset(self, event: AstrMessageEvent) -> int:
        if not isinstance(event, AiocqhttpMessageEvent):
            return 0
        if not getattr(event, "bot", None):
            return 0

        async with self._data_lock:
            users = self.data.get("users", {})
            groups = self.data.get("groups", {})
            planned_updates: list[tuple[str, str]] = []

            for group_id, group_info in groups.items():
                if not isinstance(group_info, dict):
                    continue
                members = group_info.get("members", {})
                if not isinstance(members, dict):
                    continue

                for member_user_id, member_info in members.items():
                    if not isinstance(member_info, dict):
                        continue
                    current_title = self._normalize_text(member_info.get("negative_title"))
                    is_negative_user = users.get(member_user_id, {}).get("points", 0) < 0
                    if current_title or is_negative_user:
                        planned_updates.append((str(group_id), str(member_user_id)))

        if not planned_updates:
            return 0

        cleared_count = 0
        for group_id, member_user_id in planned_updates:
            try:
                await event.bot.set_group_special_title(
                    group_id=int(group_id),
                    user_id=int(member_user_id),
                    special_title="",
                    duration=-1,
                )
                cleared_count += 1
            except Exception as exc:
                logger.warning(
                    f"清空数据前移除负分头衔失败: group={group_id}, user={member_user_id}, error={exc}"
                )

        return cleared_count

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
                target_uid = self._normalize_user_id(uid_match.group(1))

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

                yield self._plain_result(event, 
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
                    user_info["points"] -= sign_cfg["fortune_event_points"]
                    fortune_points_delta = sign_cfg["fortune_event_points"]

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
            birthday_cfg = self._get_birthday_settings()
            blessing_text = await self._generate_birthday_blessing_text(
                event, reply_name, birthday_cfg["reward_points"]
            )
            birthday_text = (
                f"{blessing_text}获得 {birthday_cfg['reward_points']} {self._get_points_name()}，"
                f"当前共有 {total_points} {self._get_points_name()}。"
            )

        await self._refresh_negative_titles_for_user(event, user_id)

        yield self._plain_result(event,
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

    async def _handle_make_up_sign_in(self, event: AstrMessageEvent):
        """补上最近一个漏签日，只恢复签到记录，不重复发放签到奖励。"""
        user_id = str(event.get_sender_id())
        now = datetime.datetime.now()
        today_date = self._get_sign_in_business_date(now)
        today = today_date.isoformat()
        target_date = today_date - datetime.timedelta(days=1)
        target = target_date.isoformat()
        previous_target = (target_date - datetime.timedelta(days=1)).isoformat()
        month_key = today_date.strftime("%Y-%m")
        reply_name = self._get_sender_reply_name(event)
        points_name = self._get_points_name()
        sign_cfg = self._get_sign_in_settings()
        cost = sign_cfg["make_up_cost"]
        monthly_limit = sign_cfg["make_up_monthly_limit"]
        already_today = False

        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            group_member_changed = self._touch_group_member(
                event, user_id, self._get_sender_display_name(event)
            )

            last_sign_in = self._normalize_text(user_info.get("last_sign_in"))
            try:
                last_sign_in_date = datetime.date.fromisoformat(last_sign_in)
            except ValueError:
                last_sign_in_date = None

            if last_sign_in_date == target_date:
                if group_member_changed:
                    await self._save_data_locked()
                yield self._plain_result(event, f"{reply_name}昨天已经签到过了，不需要补签。")
                return

            if last_sign_in_date == today_date:
                if user_info["streak"] > 1:
                    if group_member_changed:
                        await self._save_data_locked()
                    yield self._plain_result(event, f"{reply_name}昨天已经包含在连续签到里了，不需要补签。")
                    return
                already_today = True
            elif last_sign_in_date is not None and last_sign_in_date > today_date:
                if group_member_changed:
                    await self._save_data_locked()
                yield self._plain_result(event, "签到记录日期异常，暂时无法补签，请联系管理员检查数据。")
                return

            usage_month = self._normalize_text(user_info.get("make_up_sign_in_month"))
            usage_count = self._normalize_int(
                user_info.get("make_up_sign_in_count"), 0, minimum=0
            )
            if usage_month != month_key:
                usage_count = 0

            if monthly_limit > 0 and usage_count >= monthly_limit:
                if group_member_changed:
                    await self._save_data_locked()
                yield self._plain_result(
                    event,
                    f"{reply_name}本月补签次数已用完（{monthly_limit} 次），下月再来吧。",
                )
                return

            if cost > 0 and user_info["points"] < cost:
                if group_member_changed:
                    await self._save_data_locked()
                yield self._plain_result(
                    event,
                    f"积分不足，补签需要 {cost} {points_name}，你当前只有 {user_info['points']} {points_name}。",
                )
                return

            user_info["points"] -= cost
            user_info["make_up_sign_in_month"] = month_key
            user_info["make_up_sign_in_count"] = usage_count + 1
            user_info["total_sign_in_days"] += 1
            if not user_info["first_sign_in_at"]:
                user_info["first_sign_in_at"] = target

            if already_today:
                user_info["streak"] = max(user_info["streak"], 1) + 1
            else:
                user_info["streak"] = (
                    user_info["streak"] + 1
                    if last_sign_in_date is not None
                    and last_sign_in_date.isoformat() == previous_target
                    else 1
                )
                user_info["last_sign_in"] = target

            await self._save_data_locked()
            remaining_points = user_info["points"]
            streak = user_info["streak"]
            used_count = user_info["make_up_sign_in_count"]

        await self._refresh_negative_titles_for_user(event, user_id)
        limit_text = (
            f"本月已补签 {used_count}/{monthly_limit} 次。"
            if monthly_limit > 0
            else f"本月已补签 {used_count} 次。"
        )
        yield self._plain_result(
            event,
            f"{reply_name}补签成功，已补上 {target}，消耗 {cost} {points_name}，"
            f"当前有 {remaining_points} {points_name}，已连签 {streak} 天。{limit_text}",
        )

    async def _handle_steal_points(self, event: AstrMessageEvent):
        """在群聊中尝试从指定用户处偷取积分。"""
        settings = self._get_steal_settings()
        points_name = self._get_points_name()
        sender_id = self._normalize_user_id(event.get_sender_id())
        reply_name = self._get_sender_reply_name(event)
        group_id = self._get_group_id(event)

        if not settings["enabled"]:
            yield self._plain_result(event, "偷积分功能当前未开启，请联系管理员调整配置。")
            return
        if not group_id:
            yield self._plain_result(event, "偷积分仅支持群聊中使用。")
            return

        target_uid = self._extract_target_user_id(event)
        if not target_uid:
            target_match = re.search(r"\d{1,20}", self._get_command_args(event))
            if target_match:
                target_uid = self._normalize_user_id(target_match.group(0))
        if not target_uid:
            yield self._plain_result(
                event, "请指定要偷积分的用户，例如：/偷积分 @用户 或 /偷积分 QQ号。"
            )
            return
        if target_uid == sender_id:
            yield self._plain_result(event, "不能偷自己的积分。")
            return

        today = self._get_sign_in_business_date_str()
        async with self._data_lock:
            sender = self._get_user_record(sender_id)
            victim = self._get_user_record(target_uid)
            group_member_changed = self._touch_group_member(
                event, sender_id, self._get_sender_display_name(event)
            )
            # 每日计数随签到业务日刷新，避免额外的定时任务和旧数据膨胀。
            if sender.get("steal_points_date") != today:
                sender["steal_points_date"] = today
                sender["daily_steal_points_times"] = 0
            if victim.get("stolen_points_date") != today:
                victim["stolen_points_date"] = today
                victim["daily_stolen_points_times"] = 0

            used_times = self._normalize_int(
                sender.get("daily_steal_points_times"), 0, 0
            )
            stolen_times = self._normalize_int(
                victim.get("daily_stolen_points_times"), 0, 0
            )
            if (
                settings["daily_steal_limit"] > 0
                and used_times >= settings["daily_steal_limit"]
            ):
                if group_member_changed:
                    await self._save_data_locked()
                yield self._plain_result(
                    event,
                    f"{reply_name}今天已经偷过 {used_times} 次了，明天再来吧。",
                )
                return
            if (
                settings["daily_be_stolen_limit"] > 0
                and stolen_times >= settings["daily_be_stolen_limit"]
            ):
                if group_member_changed:
                    await self._save_data_locked()
                yield self._plain_result(
                    event,
                    f"这个用户今天已经被偷 {stolen_times} 次了，先放过他吧。",
                )
                return

            sender_before = sender["points"]
            victim_before = victim["points"]
            sender_times_before = used_times
            victim_times_before = stolen_times
            sender_date_before = sender.get("steal_points_date", "")
            victim_date_before = victim.get("stolen_points_date", "")
            sender["daily_steal_points_times"] = used_times + 1
            sender["steal_points_date"] = today
            victim["stolen_points_date"] = today

            success = (
                victim["points"] > 0
                and random.random() < settings["success_probability"]
            )
            if success:
                amount = random.randint(
                    settings["min_points"], settings["max_points"]
                )
                amount = min(amount, victim["points"])
                sender["points"] += amount
                victim["points"] -= amount
                result_text = (
                    f"成功偷取 {target_uid} {amount} {points_name}。"
                )
            else:
                cost = settings["failure_cost"]
                sender["points"] -= cost
                if settings["failure_cost_to_victim"] and cost > 0:
                    victim["points"] += cost
                result_text = f"偷取失败，扣除 {cost} {points_name}。"

            if success or settings["failure_counts_as_stolen"]:
                victim["daily_stolen_points_times"] = victim_times_before + 1

            save_ok = await self._save_data_locked()
            if save_ok is False:
                sender["points"] = sender_before
                victim["points"] = victim_before
                sender["daily_steal_points_times"] = sender_times_before
                victim["daily_stolen_points_times"] = victim_times_before
                sender["steal_points_date"] = sender_date_before
                victim["stolen_points_date"] = victim_date_before
                yield self._plain_result(
                    event, "偷积分记录保存失败，本次操作未生效，请稍后再试。"
                )
                return

        await self._refresh_negative_titles_for_user(event, sender_id)
        yield self._plain_result(event, result_text)

    @filter.command("群聊签到")
    async def sign_in(self, event: AstrMessageEvent):
        """每日签到以获取积分奖励。"""
        async for result in self._handle_sign_in(event):
            yield result

    @filter.command("补签")
    async def make_up_sign_in(self, event: AstrMessageEvent):
        """消耗积分补上最近一个漏签日。"""
        async for result in self._handle_make_up_sign_in(event):
            yield result

    @filter.command("偷积分")
    async def steal_points(self, event: AstrMessageEvent):
        """按配置概率从指定群成员处尝试偷取积分。"""
        async for result in self._handle_steal_points(event):
            yield result

    @filter.command("抽奖")
    async def lottery_command(self, event: AstrMessageEvent):
        """桥接抽奖命令，确保继承自 mixin 的命令在插件主类中可被框架发现。"""
        async for result in LotteryFeatureMixin.lottery(self, event):
            yield result

    @filter.command("生日签到")
    async def birthday_sign_in_command(self, event: AstrMessageEvent):
        """桥接生日签到命令，避免框架遗漏注册 mixin 中的命令方法。"""
        async for result in BirthdayFeatureMixin.birthday_sign_in(self, event):
            yield result

    @filter.command("记录生日")
    async def record_birthday_command(self, event: AstrMessageEvent):
        """桥接记录生日命令，避免框架遗漏注册 mixin 中的命令方法。"""
        async for result in BirthdayFeatureMixin.record_birthday(self, event):
            yield result

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

        yield self._plain_result(event, 
            self._format_msg(
                "query_points",
                user=reply_name,
                total=total_points,
                streak=streak,
                total_sign_in_days=total_sign_in_days,
                sign_in_status=sign_in_status,
            )
        )

    @filter.command("积分规则")
    async def points_rules(self, event: AstrMessageEvent):
        """查看当前积分获取规则。"""
        points_name = self._get_points_name()
        sign_cfg = self._get_sign_in_settings()
        birthday_cfg = self._get_birthday_settings()
        activity_cfg = self._get_activity_settings()
        steal_cfg = self._get_steal_settings()
        lottery_cfg = self._get_lottery_settings()
        special_reward_entries = self._get_special_date_reward_entries()
        exchange_items = [
            item for item in self._get_exchange_items() if item["enabled"]
        ]
        sign_in_triggers = self._get_sign_in_triggers()
        lottery_triggers = self._get_lottery_triggers()
        sign_in_examples = " / ".join(f"“{item}”" for item in sign_in_triggers[:2])
        make_up_examples = " / ".join(
            f"“{item}”" for item in self._get_make_up_sign_in_triggers()[:3]
        )
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

        if steal_cfg["enabled"]:
            steal_limit_text = (
                f"每天最多 {steal_cfg['daily_steal_limit']} 次"
                if steal_cfg["daily_steal_limit"] > 0
                else "每天不限次数"
            )
            be_stolen_limit_text = (
                f"每人每天最多计入被偷 {steal_cfg['daily_be_stolen_limit']} 次"
                if steal_cfg["daily_be_stolen_limit"] > 0
                else "每人每天被偷次数不限"
            )
            failure_count_text = (
                "，偷取失败也计入被偷次数"
                if steal_cfg["failure_counts_as_stolen"]
                else "，偷取失败不计入被偷次数"
            )
            lines.append(
                f"9. 偷积分：{steal_limit_text}，{be_stolen_limit_text}，"
                f"成功率 {steal_cfg['success_probability'] * 100:.1f}%，"
                f"成功随机获得 {steal_cfg['min_points']}~{steal_cfg['max_points']} {points_name}"
                f"{failure_count_text}"
            )
        else:
            lines.append("9. 偷积分：当前未开启")

        lines.append(f"10. 无前缀签到：发送 {sign_in_examples} 也可以直接签到")
        lines.append(
            f"补签：发送 /补签 或 {make_up_examples} 可补上最近漏签的一天，消耗 {sign_cfg['make_up_cost']} {points_name}，"
            + (
                f"每月最多 {sign_cfg['make_up_monthly_limit']} 次"
                if sign_cfg["make_up_monthly_limit"] > 0
                else "每月不限次数"
            )
        )
        if birthday_cfg["enabled"] and birthday_cfg["reward_points"] > 0:
            lines.append(
                f"11. 生日签到：发送“{birthday_cfg['sign_in_trigger']}”可获得 {birthday_cfg['reward_points']} {points_name}，每人每年一次"
            )
            if birthday_cfg["auto_broadcast_enabled"]:
                lines.append(
                    f"12. 生日播报：每天 {birthday_cfg['auto_broadcast_time']} 自动检查并播报当日寿星名单"
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
                "13. 积分抽奖："
                + "；".join(mode_lines)
                + f"；默认模式：{'个人抽奖' if lottery_cfg['default_mode'] == 'personal' else '群体抽奖'}"
            )
            lines.append(f"14. 无前缀抽奖：发送 {lottery_examples} 也可直接参与默认模式抽奖")
        if special_reward_entries:
            enabled_entry_count = len(
                [entry for entry in special_reward_entries if entry["enabled"]]
            )
            lines.append(f"15. 日期口令奖励：当前启用 {enabled_entry_count} 条词条")
        if exchange_items:
            lines.append(
                f"16. 自定义兑换物：当前有 {len(exchange_items)} 种，"
                "发送 /兑换列表 查看价格和库存"
            )
        if self._get_red_packet_settings()["enabled"]:
            lines.append("积分红包：管理员可发送固定、拼手气或口令红包，群成员发送 /抢红包 编号领取")
        negative_rule_no = 17 if exchange_items else 16
        lines.append(
            f"{negative_rule_no}. 负分规则：负分用户只能通过每日签到恢复积分，无法参与抽奖；"
            "在已记录群聊中会尽力佩戴“群女仆X号”头衔，转正后自动移除；权限不足时不影响签到恢复。"
        )
        yield self._plain_result(event, "；".join(lines))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=100000)
    async def on_private_message_remember_target(self, event: AstrMessageEvent):
        """记录适配器提供的真实私聊会话，供群内兑换安全发放。"""
        sender_id = str(event.get_sender_id())
        async with self._data_lock:
            if self._remember_private_message_target_locked(event, sender_id):
                await self._save_data_locked()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=100000)
    async def on_group_message_gain_points(self, event: AstrMessageEvent):
        """处理无前缀签到、无前缀抽奖、日期口令奖励与群聊活跃奖励。"""
        message = self._get_event_plain_text(event)
        quick_action = self._match_quick_action_from_event(event, message)
        if quick_action == "sign_in":
            event.stop_event()
            async for result in self._handle_sign_in(event):
                yield result.stop_event()
            return
        if quick_action == "make_up_sign_in":
            event.stop_event()
            async for result in self._handle_make_up_sign_in(event):
                yield result.stop_event()
            return
        if quick_action == "lottery":
            event.stop_event()
            async for result in self._handle_lottery(event, raw_args=""):
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
            yield self._plain_result(event, birthday_sign_in_message).stop_event()
            return

        if is_negative_user:
            return

        if message and not self._is_command_like_message(message):
            special_reward_message = await self._try_special_date_reward(event, message)
            if special_reward_message is not None:
                if special_reward_message:
                    yield self._plain_result(event, special_reward_message)
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

        yield self._plain_result(event, "；".join(lines))

    @filter.command("兑换头衔")
    async def exchange_title(self, event: AstrMessageEvent):
        """消耗积分兑换自己的群头衔。用法：/兑换头衔 头衔内容"""
        exchange_cfg = self._get_exchange_settings()
        points_name = self._get_points_name()

        if not exchange_cfg["title_enabled"]:
            yield self._plain_result(event, "当前未开启积分兑换头衔功能。")
            return

        err = self._ensure_qq_group_exchange(event, "兑换头衔")
        if err:
            yield self._plain_result(event, err)
            return

        raw_title = " ".join(self._get_command_args(event).split())
        if not raw_title:
            yield self._plain_result(event, "用法：/兑换头衔 头衔内容")
            return

        if len(raw_title) > exchange_cfg["title_max_length"]:
            yield self._plain_result(event, 
                f"头衔长度不能超过 {exchange_cfg['title_max_length']} 个字符。"
            )
            return

        success, remaining_points = await self._deduct_sender_points(
            event, exchange_cfg["title_cost"]
        )
        if not success:
            yield self._plain_result(event, 
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
            yield self._plain_result(event, 
                f"兑换头衔失败，已退还 {exchange_cfg['title_cost']} {points_name}。"
                f"当前余额：{refunded_points} {points_name}。"
            )
            return

        yield self._plain_result(event,
            f"兑换成功，已将您的群头衔设置为【{raw_title}】。"
            f"消耗 {exchange_cfg['title_cost']} {points_name}，剩余 {remaining_points} {points_name}。"
        )

    @filter.command("兑换列表")
    async def exchange_item_list(self, event: AstrMessageEvent):
        """查看管理员配置的通用积分兑换物。"""
        all_enabled_items = [
            item for item in self._get_exchange_items() if item["enabled"]
        ]
        sender_id = str(event.get_sender_id())
        scope_allowed = self._is_exchange_scope_allowed(event, sender_id)
        items = all_enabled_items if scope_allowed else []
        points_name = self._get_points_name()

        if not items:
            if all_enabled_items and not scope_allowed:
                yield self._plain_result(
                    event,
                    "当前兑换范围未开放给你所在的群或账号。"
                    "如需参与，请联系管理员调整适用范围。",
                )
                return
            yield self._plain_result(
                event,
                "当前还没有已上架的兑换物。管理员可在“插件 → 群积分助手 → "
                "兑换管理”中创建并启用兑换物。",
            )
            return

        async with self._data_lock:
            group_member_changed = self._touch_group_member(
                event, sender_id, self._get_sender_display_name(event)
            )
            if group_member_changed:
                await self._save_data_locked()
            redeemed_hashes = {
                item.get("content_hash")
                for item in self.data.get("exchange_redemptions", [])
                if isinstance(item, dict)
                and self._exchange_redemption_consumes_stock(item)
            }
            lines = ["【积分兑换列表】"]
            available_count = 0
            for index, item in enumerate(items, start=1):
                stock = (
                    None
                    if self._exchange_item_repeatable(item)
                    else sum(
                        1
                        for content in item["contents"]
                        if self._exchange_content_fingerprint(content)
                        not in redeemed_hashes
                    )
                )
                if stock is None or stock:
                    available_count += 1
                stock_text = "库存不限" if stock is None else (f"库存 {stock}" if stock else "暂时缺货")
                private_hint = " · 结果私聊" if item["private_only"] else ""
                lines.append(
                    f"{index}. {item['name']}：{item['cost']} {points_name} · "
                    f"{stock_text}{private_hint}"
                )
            if available_count:
                lines.append("兑换方式：/兑换 兑换物名称")
            else:
                lines.append("当前所有兑换物都在补货中，稍后再来看看吧。")

        yield event.plain_result("\n".join(lines))

    @filter.command("兑换")
    async def exchange_item(self, event: AstrMessageEvent):
        """消耗积分兑换管理员配置的兑换物。用法：/兑换 兑换物名称"""
        items = self._get_exchange_items()
        points_name = self._get_points_name()
        raw_name = self._get_command_args(event)
        query = self._normalize_command_text(raw_name)
        all_enabled_items = [item for item in items if item["enabled"]]
        sender_id = str(event.get_sender_id())
        scope_allowed = self._is_exchange_scope_allowed(event, sender_id)
        enabled_items = all_enabled_items if scope_allowed else []

        if all_enabled_items and not scope_allowed:
            yield self._plain_result(
                event,
                "当前兑换范围未开放给你所在的群或账号。"
                "如需参与，请联系管理员调整适用范围。",
            )
            return

        if not query:
            if enabled_items:
                yield event.plain_result(
                    "请在 /兑换 后填写兑换物名称。\n"
                    f"例如：/兑换 {enabled_items[0]['name']}\n"
                    "发送 /兑换列表 可查看全部名称、价格和库存。"
                )
            else:
                yield self._plain_result(
                    event,
                    "当前还没有已上架的兑换物，请稍后再试。管理员可在“插件 → "
                    "群积分助手 → 兑换管理”中添加。",
                )
            return

        item, ambiguous = self._find_exchange_item(query, enabled_items)

        if item is None:
            if ambiguous:
                normalized_query = query.casefold()
                matches = [
                    candidate["name"]
                    for candidate in enabled_items
                    if normalized_query in candidate["name"].casefold()
                ]
                displayed = "、".join(f"【{name}】" for name in matches[:4])
                more_hint = f"等 {len(matches)} 项" if len(matches) > 4 else ""
                message = (
                    f"“{query}”匹配到多个兑换物：{displayed}{more_hint}。"
                    f"请填写完整名称，例如：/兑换 {matches[0]}。"
                )
            else:
                message = (
                    f"没有找到【{query}】。名称可能有误或尚未上架；"
                    "发送 /兑换列表 可查看当前名称和库存。"
                )
            yield self._plain_result(event, message)
            return

        is_private_event = self._event_is_private_chat(event)
        should_private_deliver = bool(item["private_only"] and not is_private_event)
        delivered_content = ""
        remaining_points = 0
        result_error = ""
        redemption_record: Dict[str, Any] | None = None

        async with self._data_lock:
            self._touch_group_member(
                event, sender_id, self._get_sender_display_name(event)
            )
            self._remember_private_message_target_locked(event, sender_id)
            user_info = self._get_user_record(sender_id)
            redemptions = self.data.setdefault("exchange_redemptions", [])
            redeemed_hashes = {
                record.get("content_hash")
                for record in redemptions
                if isinstance(record, dict)
                and self._exchange_redemption_consumes_stock(record)
            }
            available_contents = [
                content
                for content in item["contents"]
                if self._exchange_item_repeatable(item)
                or self._exchange_content_fingerprint(content) not in redeemed_hashes
            ]
            if available_contents:
                delivered_content = (
                    random.choice(available_contents)
                    if self._exchange_selection_mode(item.get("selection_mode")) == "random"
                    else available_contents[0]
                )

            if not delivered_content:
                result_error = (
                    f"【{item['name']}】暂时没有可用库存，本次未扣除积分。"
                    "可发送 /兑换列表 查看其他兑换物，或稍后再试。"
                )
            elif user_info["points"] < item["cost"]:
                missing_points = item["cost"] - user_info["points"]
                result_error = (
                    f"积分不足，兑换【{item['name']}】需要 {item['cost']} {points_name}，"
                    f"您当前有 {user_info['points']} {points_name}，"
                    f"还差 {missing_points} {points_name}；本次未扣除积分。"
                )
                delivered_content = ""
            else:
                user_info["points"] -= item["cost"]
                remaining_points = user_info["points"]
                now = datetime.datetime.now().isoformat(timespec="seconds")
                redemption = {
                    "redemption_id": uuid.uuid4().hex,
                    "content_hash": self._exchange_content_fingerprint(
                        delivered_content
                    ),
                    "item_name": item["name"],
                    "user_id": sender_id,
                    "group_id": self._get_group_id(event) or "",
                    "redeemed_at": now,
                    "cost": item["cost"],
                    "repeatable": self._exchange_item_repeatable(item),
                    "delivery_status": (
                        "pending" if should_private_deliver else "delivered"
                    ),
                    "delivery_channel": (
                        "private" if item["private_only"] else "current"
                    ),
                    "delivered_at": "" if should_private_deliver else now,
                    "reset_generation": self._normalize_int(
                        self.data.get("reset_generation"), 0, minimum=0
                    ),
                }
                redemptions.append(redemption)
                redemption_record = redemption
                if not await self._save_data_locked():
                    redemptions.pop()
                    user_info["points"] += item["cost"]
                    delivered_content = ""
                    redemption_record = None
                    result_error = "兑换记录保存失败，本次未扣除积分，请稍后再试。"

        if result_error:
            yield self._plain_result(event, result_error)
            return

        message = self._format_exchange_success_message(
            item["success_template"],
            item["name"],
            self._exchange_content_display(delivered_content),
            item["cost"],
            points_name,
            remaining_points,
        )
        if should_private_deliver:
            private_send_status = await self._send_private_exchange(
                event, sender_id, message, delivered_content
            )
            if private_send_status == PRIVATE_SEND_FAILED:
                rolled_back = bool(
                    redemption_record
                    and await self._rollback_failed_private_exchange(
                        sender_id, item["cost"], redemption_record
                    )
                )
                if rolled_back:
                    yield self._plain_result(
                        event,
                        f"未能通过私聊发送【{item['name']}】，本次未扣除积分，"
                        "也未消耗库存。请先向机器人发送一条私聊消息建立会话，"
                        "再回群重新兑换。",
                    )
                else:
                    yield self._plain_result(
                        event,
                        f"【{item['name']}】私聊发送失败，且兑换状态未能自动回滚。"
                        "请暂勿重复操作并联系管理员核对。",
                    )
                return

            if private_send_status == PRIVATE_SEND_UNCERTAIN:
                if redemption_record:
                    await self._mark_exchange_delivery_status(
                        redemption_record, PRIVATE_SEND_UNCERTAIN
                    )
                yield self._plain_result(
                    event,
                    f"【{item['name']}】的私聊发送状态暂时无法确认。"
                    "为避免同一份奖励重复发放，本次积分和库存已保留；"
                    "请先检查私聊，若未收到请联系管理员核对，暂勿重复兑换。",
                )
                return

            if redemption_record and not await self._mark_exchange_delivery_status(
                redemption_record, PRIVATE_SEND_SUCCESS
            ):
                logger.warning(
                    f"[PointSystem] 兑换已私聊送达但状态保存失败: "
                    f"redemption={redemption_record.get('redemption_id', '')}"
                )
            yield self._plain_result(
                event,
                f"兑换成功！【{item['name']}】的兑换结果已通过私聊发送。"
                f"已消耗 {item['cost']} {points_name}，"
                f"剩余 {remaining_points} {points_name}。",
            )
            return

        media_components = self._exchange_content_components(message, delivered_content)
        chain_result = getattr(event, "chain_result", None)
        if len(media_components) > 1 and callable(chain_result):
            yield chain_result(media_components)
        else:
            yield event.plain_result(message)

    @filter.command("兑换设精")
    async def exchange_essence(self, event: AstrMessageEvent):
        """消耗积分将引用消息设为群精华。用法：回复消息后发送 /兑换设精"""
        exchange_cfg = self._get_exchange_settings()
        points_name = self._get_points_name()

        if not exchange_cfg["essence_enabled"]:
            yield self._plain_result(event, "当前未开启积分兑换设精功能。")
            return

        err = self._ensure_qq_group_exchange(event, "兑换设精")
        if err:
            yield self._plain_result(event, err)
            return

        reply_message_id = self._extract_reply_message_id(event)
        if reply_message_id is None:
            yield self._plain_result(event, "请先引用一条消息，再发送 /兑换设精。")
            return

        success, remaining_points = await self._deduct_sender_points(
            event, exchange_cfg["essence_cost"]
        )
        if not success:
            yield self._plain_result(event, 
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
            yield self._plain_result(event, 
                f"兑换设精失败，已退还 {exchange_cfg['essence_cost']} {points_name}。"
                f"当前余额：{refunded_points} {points_name}。"
            )
            return

        yield self._plain_result(event, 
            f"兑换成功，目标消息已设为精华。"
            f"消耗 {exchange_cfg['essence_cost']} {points_name}，剩余 {remaining_points} {points_name}。"
        )

    @filter.command("兑换禁言")
    async def exchange_mute(self, event: AstrMessageEvent):
        """消耗积分兑换禁言。默认禁自己；配置允许后可 @他人。"""
        exchange_cfg = self._get_exchange_settings()
        points_name = self._get_points_name()

        if not exchange_cfg["mute_enabled"]:
            yield self._plain_result(event, "当前未开启积分兑换禁言功能。")
            return

        err = self._ensure_qq_group_exchange(event, "兑换禁言")
        if err:
            yield self._plain_result(event, err)
            return

        target_uid = self._extract_target_user_id(event)
        if target_uid and not exchange_cfg["allow_mute_others"]:
            yield self._plain_result(event, 
                "当前配置只允许兑换自禁，若要禁言他人，请在配置中开启 allow_mute_others。"
            )
            return

        if not target_uid:
            target_uid = str(event.get_sender_id())

        if target_uid == str(getattr(event, "get_self_id", lambda: "")()):
            yield self._plain_result(event, "不能对机器人本身使用兑换禁言。")
            return

        success, remaining_points = await self._deduct_sender_points(
            event, exchange_cfg["mute_cost"]
        )
        if not success:
            yield self._plain_result(event, 
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
            yield self._plain_result(event, 
                f"兑换禁言失败，已退还 {exchange_cfg['mute_cost']} {points_name}。"
                f"当前余额：{refunded_points} {points_name}。"
            )
            return

        target_desc = (
            "自己" if target_uid == str(event.get_sender_id()) else f"用户 {target_uid}"
        )
        yield self._plain_result(event, 
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

    @filter.command("积分红包", alias={"发红包"})
    async def create_red_packet(self, event: AstrMessageEvent):
        """（积分管理员）创建固定、拼手气或口令红包。"""
        permission_error = await self._ensure_points_admin(event)
        if permission_error:
            yield self._plain_result(event, permission_error)
            return

        settings = self._get_red_packet_settings()
        if not settings["enabled"]:
            yield self._plain_result(event, "积分红包功能当前未开启，请联系管理员调整配置。")
            return

        group_id = self._get_group_id(event)
        if not group_id:
            yield self._plain_result(event, "请在群聊中创建积分红包，方便群成员领取。")
            return

        raw_args = self._normalize_command_text(self._get_command_args(event))
        parts = raw_args.split()
        if not parts:
            yield self._plain_result(event, self._red_packet_help())
            return

        packet_type = self._red_packet_type(parts[0])
        if not packet_type:
            yield self._plain_result(
                event,
                "红包类型暂不识别，请使用“固定”“拼手气”或“口令”。"
                + self._red_packet_help(),
            )
            return

        if len(parts) < 3:
            yield self._plain_result(event, self._red_packet_help())
            return

        try:
            first_amount = int(parts[1])
            count = int(parts[2])
        except (TypeError, ValueError):
            yield self._plain_result(event, "积分和份数需要填写正整数。" + self._red_packet_help())
            return

        if first_amount <= 0 or count <= 0:
            yield self._plain_result(event, "积分和份数需要填写正整数。" + self._red_packet_help())
            return
        if count > settings["max_count"]:
            yield self._plain_result(
                event,
                f"这个红包最多设置 {settings['max_count']} 份，可在 red_packet_settings.max_count 中调整。",
            )
            return

        password = ""
        if packet_type == "password":
            password = " ".join(parts[3:]).strip()
            if not password:
                yield self._plain_result(
                    event,
                    "口令红包还需要填写口令，例如：/积分红包 口令 100 5 春日快乐。",
                )
                return
            if len(password) > 80:
                yield self._plain_result(event, "口令最多 80 个字符，请换一个简短口令。")
                return

        if packet_type == "fixed":
            unit_points = first_amount
            try:
                total_points = unit_points * count
            except (OverflowError, MemoryError):
                total_points = settings["max_total_points"] + 1
        else:
            unit_points = 0
            total_points = first_amount

        if total_points > settings["max_total_points"]:
            yield self._plain_result(
                event,
                f"这个红包最多发放 {settings['max_total_points']} {self._get_points_name()}，"
                "可在 red_packet_settings.max_total_points 中调整。",
            )
            return
        if packet_type != "fixed" and total_points < count:
            yield self._plain_result(
                event,
                f"拼手气或口令红包的总积分至少需要 {count} {self._get_points_name()}，"
                "这样每一份都能正常发放。",
            )
            return

        packet_id = ""
        now = datetime.datetime.now()
        expires_at = ""
        if settings["expire_minutes"] > 0:
            expires_at = (
                now + datetime.timedelta(minutes=settings["expire_minutes"])
            ).isoformat(timespec="seconds")

        create_error = ""
        async with self._data_lock:
            packets = self.data.setdefault("red_packets", [])
            existing_ids = {
                self._normalize_text(item.get("packet_id")).casefold()
                for item in packets
                if isinstance(item, dict)
            }
            for _ in range(5):
                candidate = uuid.uuid4().hex[:8].casefold()
                if candidate not in existing_ids:
                    packet_id = candidate
                    break
            if not packet_id:
                create_error = "暂时无法生成红包编号，请稍后再试。"
            else:
                packet = {
                    "packet_id": packet_id,
                    "packet_type": packet_type,
                    "total_points": total_points,
                    "remaining_points": total_points,
                    "total_count": count,
                    "remaining_count": count,
                    "unit_points": unit_points,
                    "claimed_user_ids": [],
                    "group_id": group_id,
                    "sender_id": self._normalize_user_id(event.get_sender_id()),
                    "password_hash": self._red_packet_password_hash(password)
                    if packet_type == "password"
                    else "",
                    "created_at": now.isoformat(timespec="seconds"),
                    "expires_at": expires_at,
                    "reset_generation": self._normalize_int(
                        self.data.get("reset_generation"), 0, minimum=0
                    ),
                }
                packets.append(packet)
                if not await self._save_data_locked():
                    packets.pop()
                    create_error = "红包保存失败，本次没有发出积分，请稍后再试。"

        if create_error:
            yield self._plain_result(event, create_error)
            return

        log_operations, _ = self._get_admin_settings()
        if log_operations:
            logger.info(
                f"管理员 {event.get_sender_id()} 创建积分红包: packet={packet_id}, "
                f"type={packet_type}, total={total_points}, count={count}, group={group_id}"
            )

        type_text = {
            "fixed": f"每份 {unit_points} {self._get_points_name()}",
            "lucky": f"总额 {total_points} {self._get_points_name()}",
            "password": f"总额 {total_points} {self._get_points_name()}（需口令）",
        }[packet_type]
        claim_hint = (
            "群成员发送 /抢红包 口令领取。"
            if packet_type == "password"
            else "群成员发送 /抢红包 即可领取。"
        )
        yield self._plain_result(
            event,
            f"积分红包已发出，编号 {packet_id.upper()}，{type_text}，共 {count} 份。"
            + claim_hint,
        )

    @filter.command("抢红包", alias={"领红包", "红包"})
    async def claim_red_packet(self, event: AstrMessageEvent):
        """领取群内积分红包；口令红包需附带口令。"""
        group_id = self._get_group_id(event)
        if not group_id:
            yield self._plain_result(event, "请在红包所在群聊中领取积分红包。")
            return

        raw_args = self._normalize_command_text(self._get_command_args(event))
        packet_id = ""
        supplied_password = ""
        if raw_args:
            parts = raw_args.split(maxsplit=1)
            candidate_id = parts[0].casefold()
            if re.fullmatch(r"[a-z0-9]{6,32}", candidate_id):
                packet_id = candidate_id
                supplied_password = parts[1].strip() if len(parts) > 1 else ""
            else:
                supplied_password = raw_args

        sender_id = self._normalize_user_id(event.get_sender_id())
        points_name = self._get_points_name()
        amount = 0
        remaining_count = 0
        claim_error = ""
        lucky_winner_name = ""
        lucky_winner_amount = 0
        async with self._data_lock:
            packets = self.data.setdefault("red_packets", [])
            if packet_id:
                packet = next(
                    (
                        item
                        for item in packets
                        if isinstance(item, dict)
                        and self._normalize_text(item.get("packet_id")).casefold()
                        == packet_id
                    ),
                    None,
                )
            else:
                packet = next(
                    (
                        item
                        for item in reversed(packets)
                        if isinstance(item, dict)
                        and self._normalize_user_id(item.get("group_id")) == group_id
                        and (
                            not supplied_password
                            or item.get("packet_type") == "password"
                        )
                        and not self._red_packet_expired(item)
                        and self._normalize_int(
                            item.get("remaining_count"), 0, minimum=0
                        ) > 0
                    ),
                    None,
                )
            if packet is None:
                claim_error = (
                    "当前群没有可领取的积分红包，等管理员发一个新的吧。"
                    if not packet_id
                    else "没有找到这个红包编号，请核对后再试。"
                )
            elif self._normalize_user_id(packet.get("group_id")) != group_id:
                claim_error = "这个红包不在当前群，请回到发红包的群里领取。"
            elif self._red_packet_expired(packet):
                claim_error = "这个红包已经过期了，看看群里有没有新的红包吧。"
            elif self._normalize_int(packet.get("remaining_count"), 0, minimum=0) <= 0:
                claim_error = "这个红包已经被领完了，下次早点来。"
            else:
                claimed_user_ids = packet.setdefault("claimed_user_ids", [])
                if sender_id in claimed_user_ids:
                    claim_error = "你已经领过这个红包了，每人只能领取一次。"
                elif packet.get("packet_type") == "password":
                    expected_hash = self._normalize_text(packet.get("password_hash"))
                    supplied_hash = self._red_packet_password_hash(supplied_password)
                    if not supplied_password or not hmac.compare_digest(
                        expected_hash, supplied_hash
                    ):
                        claim_error = "口令不正确，请核对后再试。"

                if not claim_error:
                    amount = self._red_packet_claim_amount(packet)
                    if amount <= 0:
                        claim_error = "这个红包暂时没有可领取的积分。"
                    else:
                        self._touch_group_member(
                            event, sender_id, self._get_sender_display_name(event)
                        )
                        old_points = self._get_user_record(sender_id)["points"]
                        old_remaining_points = packet["remaining_points"]
                        old_remaining_count = packet["remaining_count"]
                        packet["remaining_points"] -= amount
                        packet["remaining_count"] -= 1
                        claimed_user_ids.append(sender_id)
                        claimed_records = packet.setdefault("claimed_records", [])
                        claimed_records.append(
                            {
                                "user_id": sender_id,
                                "display_name": self._safe_reply_name(
                                    self._get_sender_display_name(event)
                                ),
                                "amount": amount,
                            }
                        )
                        self._get_user_record(sender_id)["points"] += amount
                        if not await self._save_data_locked():
                            packet["remaining_points"] = old_remaining_points
                            packet["remaining_count"] = old_remaining_count
                            claimed_user_ids.pop()
                            claimed_records.pop()
                            self._get_user_record(sender_id)["points"] = old_points
                            claim_error = "红包领取记录保存失败，请稍后再试。"
                        else:
                            remaining_count = packet["remaining_count"]
                            if (
                                packet.get("packet_type") == "lucky"
                                and remaining_count == 0
                                and len(claimed_records)
                                >= self._normalize_int(
                                    packet.get("total_count"), 0, minimum=0
                                )
                            ):
                                winner = max(
                                    claimed_records,
                                    key=lambda record: record.get("amount", 0),
                                )
                                lucky_winner_name = self._safe_reply_name(
                                    winner.get("display_name")
                                    or f"用户({self._mask_user_id(winner.get('user_id', ''))})"
                                )
                                lucky_winner_amount = self._normalize_int(
                                    winner.get("amount"), amount, minimum=1
                                )

        if claim_error:
            yield self._plain_result(event, claim_error)
            return

        if remaining_count:
            suffix = f"还剩 {remaining_count} 份。"
        else:
            suffix = "这个红包已经领完了。"
            if lucky_winner_name:
                suffix += (
                    f"手气王是 {lucky_winner_name}，领到 {lucky_winner_amount} "
                    f"{points_name}。"
                )
        yield self._plain_result(
            event,
            f"恭喜你领到 {amount} {points_name}！{suffix}",
        )

    @filter.command("清空所有数据")
    async def clear_all_points_data(self, event: AstrMessageEvent):
        """（积分管理员）清空全部积分数据。用法：/清空所有数据 确认"""
        permission_error = await self._ensure_points_admin(event)
        if permission_error:
            yield self._plain_result(event, permission_error)
            return

        if self._normalize_text(self._get_command_args(event)) != "确认":
            yield self._plain_result(
                event,
                "该操作会清空所有积分、抽奖、红包、生日和群记录（兑换记录会保留），"
                "请发送 /清空所有数据 确认 继续。",
            )
            return

        log_operations, _ = self._get_admin_settings()
        cleared_title_count = await self._clear_negative_titles_before_reset(event)

        async with self._data_lock:
            old_user_count = len(self.data.get("users", {}))
            old_group_count = len(self.data.get("groups", {}))
            exchange_redemptions = self.data.get("exchange_redemptions", [])
            next_reset_generation = self._normalize_int(
                self.data.get("reset_generation"), 0, minimum=0
            ) + 1
            self.data = self._new_store()
            self.data["exchange_redemptions"] = exchange_redemptions
            self.data["reset_generation"] = next_reset_generation
            await self._save_data_locked()

        if log_operations:
            logger.warning(
                f"管理员 {event.get_sender_id()} 清空了全部积分数据，重置用户 {old_user_count} 个，"
                f"重置群 {old_group_count} 个，尝试移除头衔 {cleared_title_count} 个"
            )

        yield self._plain_result(
            event,
            f"已清空全部积分数据，重置 {old_user_count} 个用户、{old_group_count} 个群记录，"
            f"并尝试移除 {cleared_title_count} 个负分头衔。",
        )

    async def _admin_modify_points(self, event: AstrMessageEvent, is_add: bool):
        """积分管理员修改积分的统一处理函数"""
        permission_error = await self._ensure_points_admin(event)
        if permission_error:
            yield self._plain_result(event, permission_error)
            return

        log_operations, max_limit = self._get_admin_settings()
        points_name = self._get_points_name()
        command_name = self._get_command_name(event)
        target_uid, amount = self._parse_manual_points_args(event)

        if amount is None or not target_uid:
            yield self._plain_result(event, 
                f"用法：{command_name} @用户 数量；或：{command_name} QQ号 数量"
            )
            return

        if amount <= 0:
            yield self._plain_result(event, "错误：数值必须是正整数。")
            return

        if amount > max_limit:
            yield self._plain_result(event, 
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

            await self._save_data_locked()
            current_points = user_info["points"]

        if log_operations:
            logger.info(
                f"管理员 {event.get_sender_id()} 为用户 {target_uid} {action_str}了 "
                f"{amount} {points_name}，积分 {before_points} -> {current_points}"
            )

        await self._refresh_negative_titles_for_user(event, target_uid)

        yield self._plain_result(event, 
            f"成功为用户 {target_uid} {action_str}了 {amount} {points_name}。"
            f"该用户当前总积分为：{current_points}"
        )

    async def terminate(self):
        """插件卸载时保存一次数据"""
        if self.page_api is not None:
            self.page_api.unregister_routes()
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
            if await self._save_data_locked():
                logger.info("积分系统数据已安全保存。")
            else:
                logger.error("卸载保存积分数据失败。")
