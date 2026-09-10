# -*- coding: utf-8 -*-
import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from main import PointSystemPlugin
from page_api import PointSystemPageApi


class FakeEvent:
    def __init__(self, group_id="100", sender_id="1", sender_name=None, self_id=""):
        self._group_id = group_id
        self._sender_id = sender_id
        self._sender_name = sender_name
        self.message_str = "/积分榜"
        self.message_obj = SimpleNamespace(message=[], self_id=self_id)

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return self._sender_name or f"用户{self._sender_id}"

    def get_self_id(self):
        return self.message_obj.self_id


class FakePlugin(PointSystemPlugin):
    def __init__(self):
        self.config = {
            "points_name": "积分",
            "leaderboard_settings": {"display_limit": 10, "show_self_rank": True},
        }
        self.data = {
            "users": {
                "1": {"points": 100},
                "2": {"points": 200},
                "3": {"points": 300},
            },
            "groups": {
                "100": {"members": {"1": {"display_name": "本群用户"}}},
                "200": {"members": {"2": {"display_name": "另一群用户"}}},
            },
        }
        self._data_lock = asyncio.Lock()

    def _plain_result(self, event, text):
        return text

    async def _save_data_locked(self):
        return True

    def _touch_group_member(self, event, user_id, display_name=None):
        return False

    def _get_sender_display_name(self, event):
        return event.get_sender_name()


class LeaderboardTests(unittest.TestCase):
    def test_group_leaderboard_only_includes_current_group_members(self):
        plugin = FakePlugin()

        async def run():
            return [item async for item in plugin.leaderboard(FakeEvent("100", "1"))]

        message = asyncio.run(run())[0]
        self.assertIn("本群用户", message)
        self.assertNotIn("另一群用户", message)
        self.assertNotIn("用户(3***", message)

    def test_empty_group_does_not_fallback_to_global_leaderboard(self):
        plugin = FakePlugin()
        plugin.data["groups"]["100"]["members"] = {}

        async def run():
            return [item async for item in plugin.leaderboard(FakeEvent("100", "1"))]

        message = asyncio.run(run())[0]
        self.assertIn("本群积分排行榜", message)
        self.assertIn("暂无排行数据", message)
        self.assertNotIn("另一群用户", message)

    def test_private_leaderboard_is_rejected_instead_of_showing_global_users(self):
        plugin = FakePlugin()

        async def run():
            return [item async for item in plugin.leaderboard(FakeEvent("", "1"))]

        message = asyncio.run(run())[0]
        self.assertIn("仅支持群聊", message)
        self.assertNotIn("总排行榜", message)


class MemberIdentityTests(unittest.TestCase):
    def setUp(self):
        self.plugin = object.__new__(PointSystemPlugin)
        self.plugin.config = {"points_name": "积分"}
        self.plugin._data_lock = asyncio.Lock()
        self.plugin._plain_result = lambda event, text: text
        self.plugin._save_data_locked = AsyncMock(return_value=True)
        self.plugin.data = {
            "users": {
                "1": self.plugin._normalize_user_record({"points": 75}),
                "99": self.plugin._normalize_user_record({"points": 9780}),
            },
            "groups": {
                "100": {
                    "members": {
                        "1": {"display_name": "群成员", "updated_at": "2026-08-28T12:00:00"},
                        "99": {
                            "display_name": "群成员",
                            "updated_at": "2026-08-31T12:00:00",
                            "negative_title": "原头衔",
                        },
                    }
                }
            },
        }

    def test_bot_query_does_not_borrow_the_requesting_members_name(self):
        event = FakeEvent(sender_id="99", sender_name="群成员", self_id="99")

        async def run():
            return [item async for item in self.plugin.query_points(event)]

        message = asyncio.run(run())[0]
        self.assertIn("机器人（99）", message)
        self.assertIn("9780 积分", message)
        self.assertNotIn("群成员", message)
        self.assertEqual(self.plugin.data["users"]["1"]["points"], 75)

    def test_group_activity_repairs_names_for_both_leaderboards(self):
        before_users = copy.deepcopy(self.plugin.data["users"])
        event = FakeEvent(sender_name="群成员", self_id="99")

        changed = self.plugin._touch_group_member(event, "1", "群成员")

        self.assertTrue(changed)
        rankings = {
            user_id: (record["points"], name)
            for user_id, record, name in self.plugin._get_group_rankings("100")
        }
        self.assertEqual(rankings["1"], (75, "群成员"))
        self.assertEqual(rankings["99"], (9780, "机器人（99）"))
        api = PointSystemPageApi(self.plugin)
        for group_id in ("", "100"):
            with self.subTest(group_id=group_id):
                leaderboard = api._dashboard_view([], group_id=group_id)["leaderboard"]
                named_members = [row for row in leaderboard if row["display_name"] == "群成员"]
                self.assertEqual(len(named_members), 1)
                self.assertEqual(named_members[0]["user_id"], "1")
                self.assertEqual(named_members[0]["points"], 75)
        self.assertEqual(self.plugin.data["users"], before_users)
        self.assertEqual(
            self.plugin.data["groups"]["100"]["members"]["99"]["negative_title"],
            "原头衔",
        )
        self.assertFalse(self.plugin._touch_group_member(event, "1", "群成员"))

    def test_first_bot_entry_ignores_borrowed_display_name(self):
        del self.plugin.data["groups"]["100"]["members"]["99"]
        event = FakeEvent(sender_id="99", sender_name="群成员", self_id="99")

        self.plugin._touch_group_member(event, "99", "群成员")

        member = self.plugin.data["groups"]["100"]["members"]["99"]
        self.assertEqual(member["display_name"], "机器人（99）")


if __name__ == "__main__":
    unittest.main()
