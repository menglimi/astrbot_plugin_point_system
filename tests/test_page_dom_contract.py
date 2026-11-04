# -*- coding: utf-8 -*-
"""页面 DOM 契约：app.js 引用的元素 id 必须出现在 index.html 中。

背景：兑换管理页曾出现首屏空白，原因是 app.js 直接对不存在的节点调用
addEventListener，异常中断了初始化流程。这里把「JS 引用 -> HTML 定义」的
对应关系固化成断言，新增引用而未补页面节点时会直接失败。
"""
import re
import unittest
from pathlib import Path

PAGE_DIR = Path(__file__).resolve().parent.parent / "pages" / "兑换管理"

# 页面暂未提供的可选控件：内容类型、重复兑换、发放顺序与媒体内容编辑。
# 这些 id 只允许通过可空绑定（on(...)）或判空访问，补齐页面控件后需从此处移除。
DEFERRED_IDS = {
    "itemContentType",
    "itemRepeatable",
    "itemSelectionMode",
    "textContentEditor",
    "mediaContentEditor",
    "contentUploadInput",
    "uploadMediaButton",
    "mediaContentList",
}

HTML_ID = re.compile(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)')""")
# 同时匹配 $("#x")、"$$("#x ...")" 与 $('#x')，跳过模板字符串拼接的动态选择器。
JS_ID_SELECTOR = re.compile(r"""\$\$?\(\s*(?:"#([A-Za-z][\w-]*)"|'#([A-Za-z][\w-]*)')""")
JS_GET_ELEMENT_BY_ID = re.compile(r"""getElementById\(\s*(?:"([A-Za-z][\w-]*)"|'([A-Za-z][\w-]*)')""")
JS_PREFIXED_SELECTOR = re.compile(r"""\$\$?\(\s*(?:"#([A-Za-z][\w-]*)[\s>]|'#([A-Za-z][\w-]*)[\s>])""")


def _first(match):
    return match.group(1) if match.group(1) is not None else match.group(2)


def html_ids(page_dir):
    text = (Path(page_dir) / "index.html").read_text(encoding="utf-8")
    return {_first(match) for match in HTML_ID.finditer(text) if _first(match)}


def js_id_references(page_dir):
    """返回 {id: 首次出现的行号}。"""
    text = (Path(page_dir) / "app.js").read_text(encoding="utf-8")
    references = {}
    patterns = (JS_ID_SELECTOR, JS_GET_ELEMENT_BY_ID, JS_PREFIXED_SELECTOR)
    for pattern in patterns:
        for match in pattern.finditer(text):
            node_id = _first(match)
            line = text.count("\n", 0, match.start()) + 1
            references.setdefault(node_id, line)
    return references


def direct_chain_ids(page_dir, ids):
    """找出仍以 `$("#id").` 形式直接解引用（假设节点必然存在）的 id。"""
    text = (Path(page_dir) / "app.js").read_text(encoding="utf-8")
    chained = {}
    for node_id in ids:
        pattern = re.compile(r"""\$\("#%s"\)\s*\.""" % re.escape(node_id))
        match = pattern.search(text)
        if match:
            chained[node_id] = text.count("\n", 0, match.start()) + 1
    return chained


def audit_dom_contract(page_dir=PAGE_DIR, deferred=DEFERRED_IDS):
    defined = html_ids(page_dir)
    referenced = js_id_references(page_dir)
    missing = {node_id: line for node_id, line in referenced.items() if node_id not in defined}
    undeclared = {node_id: line for node_id, line in missing.items() if node_id not in deferred}
    chained = direct_chain_ids(page_dir, set(missing))
    return {
        "defined": defined,
        "referenced": referenced,
        "missing": missing,
        "undeclared": undeclared,
        "deferred": set(deferred),
        "chained": chained,
    }


class PageDomContractTest(unittest.TestCase):
    def setUp(self):
        self.result = audit_dom_contract()

    def test_page_files_exist(self):
        self.assertTrue((PAGE_DIR / "index.html").is_file())
        self.assertTrue((PAGE_DIR / "app.js").is_file())

    def test_html_ids_are_unique_and_parsed(self):
        text = (PAGE_DIR / "index.html").read_text(encoding="utf-8")
        found = [match.group(1) or match.group(2) for match in HTML_ID.finditer(text)]
        duplicates = sorted({node_id for node_id in found if found.count(node_id) > 1})
        self.assertEqual([], duplicates, "index.html 出现重复 id")
        # 防止解析失效导致断言空转
        self.assertGreater(len(self.result["defined"]), 40)
        self.assertGreater(len(self.result["referenced"]), 40)

    def test_js_referenced_ids_exist_in_html(self):
        undeclared = self.result["undeclared"]
        detail = "\n".join(f"  app.js:{line} 引用了 index.html 中不存在的 #{node_id}" for node_id, line in sorted(undeclared.items(), key=lambda entry: entry[1]))
        self.assertEqual({}, undeclared, f"存在未声明的缺失 id：\n{detail}")

    def test_deferred_ids_are_real_gaps(self):
        """延后清单只能覆盖真实缺口，避免用它掩盖新增的缺失节点。"""
        stale = self.result["deferred"] - set(self.result["missing"])
        self.assertEqual(set(), stale, "以下 id 已在 index.html 中定义，请从 DEFERRED_IDS 移除：" + ", ".join(sorted(stale)))

    def test_deferred_ids_are_never_dereferenced_directly(self):
        chained = self.result["chained"]
        detail = "\n".join(f"  app.js:{line} 直接解引用 #{node_id}" for node_id, line in sorted(chained.items(), key=lambda entry: entry[1]))
        self.assertEqual({}, chained, "可选控件必须以判空或 on(...) 方式访问：\n" + detail)


if __name__ == "__main__":
    unittest.main()
