import datetime
import random
import re
from typing import Any, Dict

from astrbot.api.event import AstrMessageEvent, filter


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


class LotteryFeatureMixin:
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

    def _resolve_lottery_mode(
        self, raw_args: str, lottery_cfg: Dict[str, Any]
    ) -> str:
        first_arg = raw_args.strip().split(maxsplit=1)[0].lower() if raw_args else ""
        if first_arg in {"个人", "单人", "personal", "single"}:
            return "personal"
        if first_arg in {"群体", "团体", "group", "shared"}:
            return "group"
        return lottery_cfg["default_mode"]

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

    @filter.command("抽奖")
    async def lottery(self, event: AstrMessageEvent):
        """消耗积分进行一次抽奖。"""
        lottery_cfg = self._get_lottery_settings()
        points_name = self._get_points_name()

        if not lottery_cfg["enabled"]:
            yield self._plain_result(event, "当前未开启积分抽奖功能。")
            return

        mode = self._resolve_lottery_mode(self._get_command_args(event), lottery_cfg)
        if mode == "personal" and not lottery_cfg["personal_enabled"]:
            yield self._plain_result(event, "当前未开启个人抽奖，请使用 /抽奖 群体 或在配置中打开个人抽奖开关。")
            return
        if mode == "group" and not lottery_cfg["group_enabled"]:
            yield self._plain_result(event, "当前未开启群体抽奖，请使用 /抽奖 个人 或在配置中打开群体抽奖开关。")
            return

        group_id = self._get_group_id(event)
        if mode == "group" and not group_id:
            yield self._plain_result(event, "群体抽奖仅支持群聊中使用。")
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
            yield self._plain_result(
                event,
                f"{reply_name}{self._get_negative_debt_message()}当前只能通过每日签到恢复积分，暂时无法参与抽奖。",
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

        yield self._plain_result(event, self._single_line_message(message))
