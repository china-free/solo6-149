from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Header,
    Footer,
    Input,
    Label,
    Static,
    TextArea,
    Tree,
)
from textual.widgets.tree import TreeNode

from flattener import (
    apply_mapping,
    flatten_to_rows,
    pivot_object_array,
    rows_to_csv,
)
from path_parser import parse_input


def build_tree(data: Any, node: TreeNode, name: str = 'root') -> None:
    if isinstance(data, dict):
        if not data:
            child = node.add(f'📁 {name}  {{}}', expand=False)
        else:
            child = node.add(f'📁 {name}', expand=True)
            for k, v in data.items():
                build_tree(v, child, str(k))
    elif isinstance(data, list):
        if not data:
            child = node.add(f'📋 {name}  []', expand=False)
        else:
            child = node.add(f'📋 {name}  [{len(data)} items]', expand=True)
            for i, item in enumerate(data[:20]):
                build_tree(item, child, f'[{i}]')
            if len(data) > 20:
                child.add(f'  ... and {len(data) - 20} more items', expand=False)
    else:
        value_str = json.dumps(data, ensure_ascii=False)
        if len(value_str) > 80:
            value_str = value_str[:77] + '...'
        icon = '🔢' if isinstance(data, (int, float)) else '🅰️' if isinstance(data, str) else '❓'
        node.add(f'{icon} {name}: {value_str}', expand=False)


DEFAULT_MAPPING_HINT = """\
# 输入映射规则 / JSONPath，按 Ctrl+Enter 导出 CSV
# 示例1 - 简单列映射:
#   id = user.id
#   name = user.name
#   email = contact.email

# 示例2 - 指定数组展开:
#   from orders

# 示例3 - 行列转换 (pivot):
#   pivot(attr_name, attr_value)

# 示例4 - JSONPath 过滤:
#   $[?(@.price > 100)]
"""


class JsonTree(Vertical):
    BORDER_TITLE = "JSON 结构"

    def __init__(self, data: Any, **kwargs):
        super().__init__(**kwargs)
        self._json_data = data

    def compose(self) -> ComposeResult:
        tree: Tree[Dict] = Tree("JSON")
        tree.show_root = False
        yield tree

    def on_mount(self) -> None:
        tree = self.query_one(Tree)
        tree.clear()
        root = tree.root
        build_tree(self._json_data, root)
        if root.children:
            root.children[0].expand()


class PreviewPanel(VerticalScroll):
    BORDER_TITLE = "CSV 预览 (前 20 行)"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._content = Static("", id="preview_content")

    def compose(self) -> ComposeResult:
        yield self._content

    def update_preview(self, csv_text: str, row_count: int) -> None:
        lines = csv_text.strip().split('\n')
        preview_lines = lines[:21]
        if len(lines) > 21:
            preview_lines.append(f"\n... (共 {row_count} 行，仅显示前 20 行)")
        self._content.update("\n".join(preview_lines))


class ErrorBar(Static):
    """红色错误提示条：默认隐藏，解析失败时醒目展示友好错误信息。"""

    DEFAULT_CSS = """
    ErrorBar {
        height: auto;
        max-height: 6;
        min-height: 0;
        border: tall red;
        background: #5c1a1a;
        color: #ffd6d6;
        padding: 0 1;
        text-style: bold;
        display: none;
    }
    ErrorBar.-visible {
        display: block;
    }
    """

    def __init__(self, **kwargs):
        super().__init__("", id="error_bar", **kwargs)

    def show_error(self, stage: str, exc: BaseException) -> None:
        exc_name = type(exc).__name__
        msg = str(exc).strip() or "(无详细信息)"
        first_line = msg.split("\n", 1)[0]
        if len(first_line) > 120:
            first_line = first_line[:117] + "..."
        self.update(f"[{stage}] {exc_name}: {first_line}")
        self.add_class("-visible")
        self.display = True

    def clear_error(self) -> None:
        self.update("")
        self.remove_class("-visible")
        self.display = False


DEBOUNCE_DELAY = 0.4


class JsonToCsvApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "JSON → CSV 转换器"
    BINDINGS = [
        ("ctrl+enter", "export_csv", "导出 CSV"),
        ("ctrl+r", "refresh_preview", "刷新预览"),
        ("ctrl+q", "quit", "退出"),
    ]

    json_data: Any = None
    json_file_path: str = ""
    output_path: reactive[str] = reactive("")
    last_rows: List[Dict[str, Any]] = []
    parse_ok: reactive[bool] = reactive(True)

    def __init__(self, json_path: str, output: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.json_file_path = json_path
        with open(json_path, 'r', encoding='utf-8') as f:
            self.json_data = json.load(f)
        base_name = os.path.splitext(os.path.basename(json_path))[0]
        self.output_path = output or f"{base_name}.csv"
        self._refresh_timer: Optional[Any] = None
        self._last_error: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield JsonTree(self.json_data, id="json_tree_panel")
            with Vertical(id="right_panel"):
                yield Label("📝 输入规则 (JSONPath / 列映射 / pivot)", id="input_label")
                input_area = TextArea(
                    DEFAULT_MAPPING_HINT,
                    id="mapping_input",
                    language="markdown",
                    theme="monokai",
                )
                input_area.show_line_numbers = True
                yield input_area
                yield ErrorBar()
                yield PreviewPanel(id="preview_panel")
                with Horizontal(id="bottom_bar"):
                    yield Label(f"📄 输入: {self.json_file_path}", id="file_label")
                    yield Button("🔄 刷新预览", id="refresh_btn", variant="primary")
                    yield Button("💾 导出 CSV", id="export_btn", variant="success")
                    yield Label(f"➡️  输出: ", id="out_label")
                    yield Input(self.output_path, id="output_input", placeholder="output.csv")
        yield Footer()

    def on_mount(self) -> None:
        self.call_after_refresh(self._apply_pipeline_safe)

    @on(Button.Pressed, "#refresh_btn")
    def on_refresh_clicked(self, event: Button.Pressed) -> None:
        self._apply_now()

    @on(Button.Pressed, "#export_btn")
    def on_export_clicked(self, event: Button.Pressed) -> None:
        self.action_export_csv()

    @on(Input.Changed, "#output_input")
    def on_output_changed(self, event: Input.Changed) -> None:
        self.output_path = event.value

    @on(TextArea.Changed, "#mapping_input")
    def on_mapping_changed(self, event: TextArea.Changed) -> None:
        self._schedule_refresh(DEBOUNCE_DELAY)

    @on(Key)
    def on_key(self, event: Key) -> None:
        if event.key == "ctrl+enter":
            event.stop()
            self.action_export_csv()
        elif event.key == "ctrl+r":
            event.stop()
            self.action_refresh_preview()

    def _cancel_pending_refresh(self) -> None:
        if self._refresh_timer is not None:
            try:
                self._refresh_timer.stop()
            except Exception:
                pass
            self._refresh_timer = None

    def _schedule_refresh(self, delay: float = DEBOUNCE_DELAY) -> None:
        self._cancel_pending_refresh()
        self._refresh_timer = self.set_timer(delay, self._apply_pipeline_safe)

    def _apply_now(self) -> None:
        self._cancel_pending_refresh()
        self._apply_pipeline_safe()

    def action_refresh_preview(self) -> None:
        self._apply_now()

    def action_export_csv(self) -> None:
        self._apply_now()
        if not self.output_path:
            self.notify("请先设置输出文件路径", severity="error", timeout=3)
            return
        if not self.parse_ok:
            self.notify(
                "❌ 当前规则存在错误，已阻止导出，请修正红色提示后重试",
                severity="error",
                timeout=5,
            )
            return
        if not self.last_rows:
            self.notify("⚠️ 没有可导出的数据行", severity="warning", timeout=3)
            return
        try:
            csv_text = rows_to_csv(self.last_rows)
            with open(self.output_path, 'w', encoding='utf-8-sig', newline='') as f:
                f.write(csv_text)
            row_count = len(self.last_rows)
            col_count = len(set().union(*[set(r.keys()) for r in self.last_rows])) if self.last_rows else 0
            self.notify(
                f"✅ 导出成功！{row_count} 行 × {col_count} 列\n→ {os.path.abspath(self.output_path)}",
                severity="information",
                timeout=5,
            )
        except Exception as e:
            self.notify(f"❌ 导出失败: {type(e).__name__}: {e}", severity="error", timeout=5)

    def _get_mapping_text(self) -> str:
        try:
            return self.query_one("#mapping_input", TextArea).text
        except Exception:
            return DEFAULT_MAPPING_HINT

    def _get_error_bar(self) -> ErrorBar:
        return self.query_one("#error_bar", ErrorBar)

    def _update_status_label(self, ok: bool, row_count: int = 0, col_count: int = 0) -> None:
        try:
            file_label = self.query_one("#file_label", Label)
            if ok:
                file_label.update(
                    f"📄 {self.json_file_path}  |  📊 {row_count} 行 × {col_count} 列  |  ✅ 规则有效"
                )
            else:
                file_label.update(f"📄 {self.json_file_path}  |  ❌ 规则有误")
        except Exception:
            pass

    def _apply_pipeline_safe(self) -> None:
        stage = "解析规则"
        try:
            input_text = self._get_mapping_text()
            parsed = parse_input(input_text)
            data = self.json_data

            if parsed['jsonpath']:
                stage = "应用 JSONPath"
                from path_parser import parse_jsonpath
                jp_result = parse_jsonpath(data, parsed['jsonpath'])
                if jp_result:
                    if len(jp_result) == 1 and isinstance(jp_result[0], (dict, list)):
                        data = jp_result[0]
                    else:
                        data = jp_result

            stage = "扁平化数据"
            rows = flatten_to_rows(
                data,
                expand_arrays=True,
                primary_array=parsed['primary_array'],
            )

            if parsed['pivot']:
                stage = "行列转换 (pivot)"
                key_col, value_col = parsed['pivot']
                rows = pivot_object_array(rows, key_col, value_col)

            if parsed['mapping']:
                stage = "应用列映射"
                rows = apply_mapping(rows, parsed['mapping'])

            stage = "生成 CSV"
            csv_text = rows_to_csv(rows)

            self.last_rows = rows
            self._last_error = None
            self.parse_ok = True
            self._get_error_bar().clear_error()
            preview = self.query_one(PreviewPanel)
            preview.update_preview(csv_text, len(rows))

            row_count = len(rows)
            col_count = len(set().union(*[set(r.keys()) for r in rows])) if rows else 0
            self._update_status_label(True, row_count, col_count)

        except Exception as exc:
            self.last_rows = []
            self._last_error = f"[{stage}] {type(exc).__name__}: {exc}"
            self.parse_ok = False
            try:
                self._get_error_bar().show_error(stage, exc)
            except Exception:
                pass
            try:
                self.query_one(PreviewPanel).update_preview("(规则有误，已暂停预览)", 0)
            except Exception:
                pass
            self._update_status_label(False)
            self.notify(f"规则解析失败: {stage}", severity="error", timeout=3)


def main():
    parser = argparse.ArgumentParser(
        description="交互式 JSON → CSV 转换器 (带 TUI 界面)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
快捷键:
  Ctrl+Enter  导出 CSV
  Ctrl+R      刷新预览
  Ctrl+Q      退出

映射规则示例:
  # 列重命名
  id = user.id
  name = user.profile.name

  # 指定主数组
  from data.orders

  # 行列转换
  pivot(attr_key, attr_value)

  # JSONPath
  $[?(@.active == true)]
""",
    )
    parser.add_argument("input", nargs="?", help="输入 JSON 文件路径")
    parser.add_argument("-o", "--output", help="输出 CSV 文件路径 (默认: <输入名>.csv)")
    parser.add_argument("--no-tui", action="store_true", help="不启动 TUI，直接转换")
    parser.add_argument("--mapping", help="映射规则文本 (配合 --no-tui 使用)")

    args = parser.parse_args()

    if not args.input:
        parser.print_help()
        sys.exit(1)

    if not os.path.isfile(args.input):
        print(f"错误: 文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.no_tui:
        with open(args.input, 'r', encoding='utf-8') as f:
            data = json.load(f)

        mapping_text = args.mapping or ""
        parsed = parse_input(mapping_text)

        if parsed['jsonpath']:
            from path_parser import parse_jsonpath
            jp_result = parse_jsonpath(data, parsed['jsonpath'])
            if jp_result:
                if len(jp_result) == 1 and isinstance(jp_result[0], (dict, list)):
                    data = jp_result[0]
                else:
                    data = jp_result

        rows = flatten_to_rows(
            data,
            expand_arrays=True,
            primary_array=parsed['primary_array'],
        )

        if parsed['pivot']:
            key_col, value_col = parsed['pivot']
            rows = pivot_object_array(rows, key_col, value_col)

        if parsed['mapping']:
            rows = apply_mapping(rows, parsed['mapping'])

        csv_text = rows_to_csv(rows)
        output = args.output or os.path.splitext(args.input)[0] + ".csv"
        with open(output, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(csv_text)
        print(f"✅ 已导出 {len(rows)} 行到 {output}")
    else:
        app = JsonToCsvApp(args.input, args.output)
        app.run()


if __name__ == "__main__":
    main()
