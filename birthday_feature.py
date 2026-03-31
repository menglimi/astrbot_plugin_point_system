import asyncio
import datetime
from typing import Any, Dict

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain


class BirthdayFeatureMixin:
    async def _generate_birthday_blessing_text(
        self, event: AstrMessageEvent, reply_name: str, reward_points: int
    ) -> str:
        birthday_cfg = self._get_birthday_settings()
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
            f"群成员 {reply_name} 发送了“{birthday_cfg['sign_in_trigger']}”，"
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
        birthday_cfg = self._get_birthday_settings()
        trigger = birthday_cfg["sign_in_trigger"]
        reward_points = birthday_cfg["reward_points"]

        if not birthday_cfg["enabled"] or reward_points <= 0:
            return None

        if self._normalize_trigger_token(message) != self._normalize_trigger_token(
            trigger
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
            if not birthday_md and birthday_cfg["auto_record_when_unset"]:
                birthday_md = today_md
                user_info["birthday_md"] = birthday_md
                auto_recorded = True
            elif not birthday_md:
                if group_member_changed:
                    await self._save_data_locked()
                return f"{reply_name}还没有记录生日，请先使用 /记录生日 mm/dd。"

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

            user_info["points"] += reward_points
            user_info["last_birthday_sign_in_year"] = current_year
            total_points = user_info["points"]
            await self._save_data_locked()

        await self._refresh_negative_titles_for_user(event, user_id)

        blessing_text = await self._generate_birthday_blessing_text(
            event, reply_name, reward_points
        )
        record_text = f"已自动为你记录生日 {today_md}，" if auto_recorded else ""
        return self._single_line_message(
            f"{record_text}{blessing_text}获得 {reward_points} {self._get_points_name()}，"
            f"当前共有 {total_points} {self._get_points_name()}。"
        )

    def _apply_birthday_reward_locked(
        self, user_info: Dict[str, Any], now: datetime.datetime | None = None
    ) -> bool:
        birthday_cfg = self._get_birthday_settings()
        if not birthday_cfg["enabled"] or birthday_cfg["reward_points"] <= 0:
            return False
        if now is None:
            now = datetime.datetime.now()
        birthday_md = self._normalize_birthday_md(user_info.get("birthday_md"))
        if not birthday_md or birthday_md != self._get_today_birthday_md(now):
            return False
        current_year = str(now.year)
        if self._normalize_text(user_info.get("last_birthday_sign_in_year")) == current_year:
            return False
        user_info["points"] += birthday_cfg["reward_points"]
        user_info["last_birthday_sign_in_year"] = current_year
        return True

    async def _birthday_broadcast_loop(self) -> None:
        last_attempt_date = ""
        while not self._birthday_broadcast_stop_event.is_set():
            birthday_cfg = self._get_birthday_settings()
            if not birthday_cfg["enabled"] or not birthday_cfg["auto_broadcast_enabled"]:
                try:
                    await asyncio.wait_for(
                        self._birthday_broadcast_stop_event.wait(), timeout=300
                    )
                except asyncio.TimeoutError:
                    continue
                break

            now = datetime.datetime.now()
            hour, minute = [
                int(part) for part in birthday_cfg["auto_broadcast_time"].split(":")
            ]
            today_iso = now.date().isoformat()
            if (
                (now.hour, now.minute) >= (hour, minute)
                and last_attempt_date != today_iso
            ):
                await self._run_birthday_broadcast()
                last_attempt_date = today_iso
                now = datetime.datetime.now()
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += datetime.timedelta(days=1)

            wait_seconds = max((next_run - now).total_seconds(), 1)
            try:
                await asyncio.wait_for(
                    self._birthday_broadcast_stop_event.wait(), timeout=wait_seconds
                )
                break
            except asyncio.TimeoutError:
                continue

    async def _run_birthday_broadcast(self) -> None:
        birthday_cfg = self._get_birthday_settings()
        if not birthday_cfg["enabled"] or not birthday_cfg["auto_broadcast_enabled"]:
            return
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

        semaphore = asyncio.Semaphore(5)

        async def _send_birthday_message(group_id: str, target: str, text: str) -> str:
            async with semaphore:
                try:
                    await self.context.send_message(target, MessageChain([Plain(text)]))
                    return group_id
                except Exception as exc:
                    logger.warning(f"发送生日播报失败: group={group_id}, error={exc}")
                    return ""

        sent_group_ids = [
            group_id
            for group_id in await asyncio.gather(
                *(
                    _send_birthday_message(group_id, target, text)
                    for group_id, target, text in pending_messages
                )
            )
            if group_id
        ]

        if not sent_group_ids:
            return

        async with self._data_lock:
            groups = self.data.setdefault("groups", {})
            for group_id in sent_group_ids:
                group_info = groups.get(group_id)
                if isinstance(group_info, dict):
                    group_info["last_birthday_broadcast_date"] = today_iso
            await self._save_data_locked()

    @filter.command("生日签到")
    async def birthday_sign_in(self, event: AstrMessageEvent):
        """领取生日签到奖励；未记录生日时会自动记录为今天。"""
        birthday_cfg = self._get_birthday_settings()
        if not birthday_cfg["enabled"] or birthday_cfg["reward_points"] <= 0:
            yield self._plain_result(event, "当前未开启生日签到功能。")
            return

        message = self._normalize_text(
            event.message_str or birthday_cfg["sign_in_trigger"]
        )
        birthday_message = await self._try_birthday_sign_in(event, message)
        if birthday_message is None:
            birthday_message = (
                f"请发送 {birthday_cfg['sign_in_trigger']} 来领取生日签到奖励。"
            )
        yield self._plain_result(event, birthday_message)

    @filter.command("记录生日")
    async def record_birthday(self, event: AstrMessageEvent):
        """记录生日，格式：/记录生日 10/24"""
        raw_value = self._get_command_args(event)
        birthday_md = self._normalize_birthday_md(raw_value)
        if not birthday_md:
            yield self._plain_result(event, "用法：/记录生日 10/24")
            return

        user_id = str(event.get_sender_id())
        reply_name = self._get_sender_reply_name(event)
        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            self._touch_group_member(event, user_id, self._get_sender_display_name(event))
            user_info["birthday_md"] = birthday_md
            await self._save_data_locked()

        yield self._plain_result(event, f"{reply_name}的生日已记录为 {birthday_md}。")
