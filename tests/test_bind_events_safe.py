# -*- coding: utf-8 -*-
"""首屏加载契约：界面控件缺失或事件绑定失败时，页面仍必须发出首屏数据请求。

用一个极简 DOM 桩在 Node 中加载 app.js：桩会把指定 id 的节点返回 null，
并统计通过 window.AstrBotPluginPage.apiGet 发出的请求次数（spy）。
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PAGE_DIR = Path(__file__).resolve().parent.parent / "pages" / "兑换管理"
APP_JS = PAGE_DIR / "app.js"
HARNESS = Path(__file__).resolve().parent / "support" / "dom_harness.mjs"
NODE = shutil.which("node")


def run_harness(js_path, missing="none", items=0):
    completed = subprocess.run(
        [NODE, str(HARNESS), "--js", str(js_path), "--missing", missing, "--items", str(items)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"harness 运行失败：{completed.stderr or completed.stdout}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


@unittest.skipIf(NODE is None, "未安装 Node.js，跳过页面脚本契约测试")
class BindEventsSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 三个用例互相独立，并行执行
        with ThreadPoolExecutor(max_workers=3) as pool:
            cls.full, cls.missing_optional, cls.missing_optional_with_item = list(
                pool.map(
                    lambda arguments: run_harness(*arguments),
                    [
                        (APP_JS, "none", 0),
                        (APP_JS, "all", 0),
                        (APP_JS, "all", 1),
                    ],
                )
            )

    def test_full_dom_loads_first_paint(self):
        self.assertIsNone(self.full["threw"])
        self.assertEqual(0, self.full["bindErrors"])
        self.assertGreaterEqual(self.full["overviewCalls"], 1, "完整页面应正常发出首屏请求")
        self.assertGreater(self.full["listenerCount"], 20, "事件绑定数量异常")

    def test_missing_optional_nodes_still_load_data(self):
        self.assertIsNone(self.missing_optional["threw"], "可选控件缺失时不应抛出异常")
        self.assertEqual(0, self.missing_optional["bindErrors"], "可选控件缺失时应静默跳过")
        self.assertGreaterEqual(self.missing_optional["overviewCalls"], 1, "可选控件缺失时仍须加载首屏数据")

    def test_editor_guards_hold_when_items_exist(self):
        """列表非空时会走 renderEditor/updateContentEditor 的可选控件分支。"""
        self.assertIsNone(self.missing_optional_with_item["threw"])
        self.assertEqual(0, self.missing_optional_with_item["bindErrors"])
        self.assertGreaterEqual(self.missing_optional_with_item["overviewCalls"], 1)

    def test_unsafe_binding_failure_cannot_block_first_paint(self):
        """回归演练：把一处绑定改回危险写法后，异常必须被隔离，首屏仍要加载。"""
        source = APP_JS.read_text(encoding="utf-8")
        mutated, replaced = re.subn(
            r'on\("#itemContentType", "change", \(',
            '$("#itemContentType").addEventListener("change", (',
            source,
        )
        self.assertEqual(1, replaced, "未找到需要变异的绑定语句，请同步更新本测试")
        with tempfile.TemporaryDirectory() as temp_dir:
            mutated_path = Path(temp_dir) / "app.js"
            mutated_path.write_text(mutated, encoding="utf-8")
            result = run_harness(mutated_path, "all", 0)
        self.assertIsNone(result["threw"], "绑定异常应在 init 内被隔离")
        self.assertGreaterEqual(result["bindErrors"], 1, "绑定异常应被记录，避免静默失败")
        self.assertGreaterEqual(result["overviewCalls"], 1, "绑定异常不得阻断首屏数据加载")


if __name__ == "__main__":
    unittest.main()
