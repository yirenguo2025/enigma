"""Main window for Enigma.

Layout (top to bottom):
  - Header: project status & controls (New / Open / Close)
  - Tabs: [Encrypt] [Decrypt] [History]
  - Footer: status log

The two main flows are intentionally separated into tabs because they have
different inputs and goals. Most users will use Encrypt before sending to AI,
then Decrypt after.
"""

from __future__ import annotations

import os
import time
import traceback
from typing import Dict, List, Optional

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import file_io
from core.crypto import KeyfileError, WrongPassword
from core.decrypt import decrypt_file
from core.encrypt import encrypt_file
from core.project import Project


KEYFILE_FILTER = "Enigma 密钥文件 (*.keyfile)"
DATA_FILTER = "数据表 (*.xlsx *.xls *.csv *.tsv *.txt);;Excel (*.xlsx *.xls);;CSV/TSV (*.csv *.tsv *.txt)"


# ============================================================
# Encrypt tab
# ============================================================

class EncryptPanel(QWidget):
    """Pick a file, pick columns + prefixes, encrypt."""

    def __init__(self, get_project, log):
        super().__init__()
        self._get_project = get_project
        self._log = log
        self._workbook: Dict[str, pd.DataFrame] = {}
        self._input_path: Optional[str] = None

        root = QVBoxLayout(self)

        # File picker row
        file_row = QHBoxLayout()
        self.file_label = QLabel("尚未选择文件")
        self.file_label.setStyleSheet("color: #666;")
        pick_btn = QPushButton("选择数据表…")
        pick_btn.clicked.connect(self.pick_file)
        file_row.addWidget(pick_btn)
        file_row.addWidget(self.file_label, 1)
        root.addLayout(file_row)

        # Columns table
        self.cols_table = QTableWidget(0, 4)
        self.cols_table.setHorizontalHeaderLabels(
            ["脱敏", "Sheet", "列名", "Token 前缀（如 GAME / TYPE）"]
        )
        self.cols_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.cols_table.horizontalHeader().setStretchLastSection(True)
        self.cols_table.verticalHeader().setVisible(False)
        root.addWidget(self.cols_table, 1)

        # Action row
        action_row = QHBoxLayout()
        self.encrypt_btn = QPushButton("一键脱敏")
        self.encrypt_btn.clicked.connect(self.do_encrypt)
        self.encrypt_btn.setEnabled(False)
        action_row.addWidget(self.encrypt_btn)
        action_row.addStretch()
        root.addLayout(action_row)

        # Generated prompt area
        prompt_box = QGroupBox("脱敏完成后，可直接复制下面这段给 AI（说明哪些列被加密）")
        prompt_layout = QVBoxLayout(prompt_box)
        self.prompt_text = QPlainTextEdit()
        self.prompt_text.setReadOnly(True)
        self.prompt_text.setMaximumHeight(160)
        prompt_layout.addWidget(self.prompt_text)
        copy_btn = QPushButton("复制 Prompt")
        copy_btn.clicked.connect(self._copy_prompt)
        prompt_layout.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignRight)
        root.addWidget(prompt_box)

    def _copy_prompt(self):
        text = self.prompt_text.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self._log("已复制 Prompt 到剪贴板。")

    def pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择数据表", "", DATA_FILTER)
        if not path:
            return
        try:
            self._workbook = file_io.load(path)
        except Exception as e:
            QMessageBox.critical(self, "读取失败", str(e))
            return
        self._input_path = path
        self.file_label.setText(path)
        self.file_label.setStyleSheet("color: #000;")
        self._populate_columns()
        self.encrypt_btn.setEnabled(self._get_project() is not None)

    def _populate_columns(self):
        # Build a row per (sheet, column).
        self.cols_table.setRowCount(0)
        proj = self._get_project()
        existing = proj.tokenizer.prefixes if proj else {}

        for sheet_name, df in self._workbook.items():
            for col in df.columns:
                row = self.cols_table.rowCount()
                self.cols_table.insertRow(row)

                cb = QCheckBox()
                cb.setStyleSheet("margin-left: 8px;")
                # Auto-check if this column was previously bound to a prefix
                if col in existing:
                    cb.setChecked(True)
                self.cols_table.setCellWidget(row, 0, cb)

                self.cols_table.setItem(row, 1, QTableWidgetItem(sheet_name))

                # Mark numeric columns so the user notices the type-loss caveat.
                is_numeric = pd.api.types.is_numeric_dtype(df[col])
                col_label = f"{col}  （数字列⚠）" if is_numeric else str(col)
                col_item = QTableWidgetItem(col_label)
                # Stash the raw column name + numeric flag for later lookup.
                col_item.setData(Qt.ItemDataRole.UserRole, (str(col), is_numeric))
                self.cols_table.setItem(row, 2, col_item)

                edit = QLineEdit()
                edit.setPlaceholderText("例：GAME（不填则用 COL{n}）")
                if col in existing:
                    edit.setText(existing[col])
                self.cols_table.setCellWidget(row, 3, edit)

    def collect_mapping(self) -> Dict[str, str]:
        """Read the table and produce {column_name: prefix}.
        We dedupe by column name (project-level): if the same column appears
        in multiple sheets, all share the same prefix.
        """
        mapping: Dict[str, str] = {}
        auto_idx = 1
        for r in range(self.cols_table.rowCount()):
            cb: QCheckBox = self.cols_table.cellWidget(r, 0)
            if not cb.isChecked():
                continue
            stored = self.cols_table.item(r, 2).data(Qt.ItemDataRole.UserRole)
            col_name = stored[0] if stored else self.cols_table.item(r, 2).text()
            edit: QLineEdit = self.cols_table.cellWidget(r, 3)
            prefix = edit.text().strip().upper()
            if not prefix:
                prefix = f"COL{auto_idx}"
                auto_idx += 1
            if col_name in mapping and mapping[col_name] != prefix:
                raise ValueError(
                    f"列「{col_name}」被指定了两个不同的前缀，请确认。"
                )
            mapping[col_name] = prefix
        return mapping

    def collect_numeric_checked(self) -> List[str]:
        """Returns the list of CHECKED column names that are numeric dtype."""
        out: List[str] = []
        for r in range(self.cols_table.rowCount()):
            cb: QCheckBox = self.cols_table.cellWidget(r, 0)
            if not cb.isChecked():
                continue
            stored = self.cols_table.item(r, 2).data(Qt.ItemDataRole.UserRole)
            if stored and stored[1]:  # is_numeric
                out.append(stored[0])
        # dedupe while preserving order
        seen = set()
        return [c for c in out if not (c in seen or seen.add(c))]

    def do_encrypt(self):
        proj = self._get_project()
        if proj is None or not self._input_path:
            return
        try:
            mapping = self.collect_mapping()
        except ValueError as e:
            QMessageBox.warning(self, "列设置有冲突", str(e))
            return
        if not mapping:
            QMessageBox.information(self, "请选择列", "请至少勾选一列要脱敏。")
            return

        # Warn if numeric columns are about to be tokenized.
        numeric_checked = self.collect_numeric_checked()
        if numeric_checked:
            cols_str = "、".join(f"「{c}」" for c in numeric_checked)
            reply = QMessageBox.warning(
                self,
                "勾选了数字列",
                f"你勾选了 {len(numeric_checked)} 个数字列：{cols_str}\n\n"
                "脱敏后这些列会变成字符串，还原后类型也是字符串（不是数字），"
                "下游 Excel 公式或求和操作可能失效。\n\n"
                "确定继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            result = encrypt_file(proj, self._input_path, mapping)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "脱敏失败", f"{type(e).__name__}: {e}")
            return

        self.prompt_text.setPlainText(result.prompt_template)
        self._log(
            f"✓ 脱敏完成：{result.output_path}（{result.rows_affected} 行；"
            f"新增 token：{result.new_tokens_per_prefix}）"
        )
        QMessageBox.information(
            self,
            "脱敏完成",
            f"已生成：\n{result.output_path}\n\n"
            f"处理 {result.rows_affected} 行；本次新增 token：{result.new_tokens_per_prefix}",
        )


# ============================================================
# Decrypt tab
# ============================================================

class DecryptPanel(QWidget):
    """Pick an AI-processed file, decrypt back."""

    def __init__(self, get_project, log):
        super().__init__()
        self._get_project = get_project
        self._log = log
        self._input_path: Optional[str] = None

        root = QVBoxLayout(self)

        info = QLabel(
            "选择一份 AI 处理后的文件（仍包含 GAME_xxx 这类占位符），点击还原。\n"
            "工具会容忍大小写、空格、引号；无法识别的占位符会标记为 [⚠未识别]。"
        )
        info.setStyleSheet("color: #555;")
        info.setWordWrap(True)
        root.addWidget(info)

        file_row = QHBoxLayout()
        self.file_label = QLabel("尚未选择文件")
        self.file_label.setStyleSheet("color: #666;")
        pick_btn = QPushButton("选择 AI 处理后的文件…")
        pick_btn.clicked.connect(self.pick_file)
        file_row.addWidget(pick_btn)
        file_row.addWidget(self.file_label, 1)
        root.addLayout(file_row)

        self.decrypt_btn = QPushButton("一键还原")
        self.decrypt_btn.clicked.connect(self.do_decrypt)
        self.decrypt_btn.setEnabled(False)
        root.addWidget(self.decrypt_btn, 0, Qt.AlignmentFlag.AlignLeft)

        # Report area
        report_box = QGroupBox("还原报告")
        report_layout = QVBoxLayout(report_box)
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        report_layout.addWidget(self.report_text)
        root.addWidget(report_box, 1)

    def pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 AI 处理后的文件", "", DATA_FILTER)
        if not path:
            return
        self._input_path = path
        self.file_label.setText(path)
        self.file_label.setStyleSheet("color: #000;")
        self.decrypt_btn.setEnabled(self._get_project() is not None)

    def do_decrypt(self):
        proj = self._get_project()
        if proj is None or not self._input_path:
            return
        try:
            result = decrypt_file(proj, self._input_path)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "还原失败", f"{type(e).__name__}: {e}")
            return

        lines = [
            f"输出文件：{result.output_path}",
            f"处理行数：{result.rows_processed}",
            f"成功还原 token 数：{result.tokens_restored}",
            f"涉及列：{', '.join(result.columns_touched) or '（无）'}",
        ]
        if result.unknown_tokens:
            lines.append("")
            lines.append("以下 Token 在密钥中找不到（可能是 AI 编造的，已加 [⚠未识别] 标记）：")
            for tok, cnt in sorted(result.unknown_tokens.items(), key=lambda kv: -kv[1]):
                lines.append(f"  - {tok}  (出现 {cnt} 次)")
        else:
            lines.append("\n所有 Token 均已成功还原 ✓")
        self.report_text.setPlainText("\n".join(lines))
        self._log(
            f"✓ 还原完成：{result.output_path}（{result.tokens_restored} 个 token，"
            f"{len(result.unknown_tokens)} 个未识别）"
        )


# ============================================================
# History tab
# ============================================================

class HistoryPanel(QWidget):
    def __init__(self, get_project):
        super().__init__()
        self._get_project = get_project

        root = QVBoxLayout(self)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        root.addWidget(refresh, 0, Qt.AlignmentFlag.AlignLeft)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["时间", "操作", "源文件", "输出文件", "行数"])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 1)

    def refresh(self):
        proj = self._get_project()
        if proj is None:
            self.table.setRowCount(0)
            return
        self.table.setRowCount(0)
        for h in reversed(proj.history):
            row = self.table.rowCount()
            self.table.insertRow(row)
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(h.timestamp))
            self.table.setItem(row, 0, QTableWidgetItem(ts))
            label = "脱敏" if h.action == "encrypt" else "还原"
            self.table.setItem(row, 1, QTableWidgetItem(label))
            self.table.setItem(row, 2, QTableWidgetItem(os.path.basename(h.source_file)))
            self.table.setItem(row, 3, QTableWidgetItem(os.path.basename(h.output_file)))
            self.table.setItem(row, 4, QTableWidgetItem(str(h.rows_affected)))


# ============================================================
# Main window
# ============================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Enigma — 本地数据脱敏工具")
        self.resize(960, 720)

        self._project: Optional[Project] = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Header: project state
        header = QHBoxLayout()
        self.project_label = QLabel("当前项目：（未打开）")
        font: QFont = self.project_label.font()
        font.setBold(True)
        self.project_label.setFont(font)
        header.addWidget(self.project_label, 1)

        self.new_btn = QPushButton("新建项目…")
        self.new_btn.clicked.connect(self.new_project)
        self.open_btn = QPushButton("打开项目…")
        self.open_btn.clicked.connect(self.open_project)
        self.close_btn = QPushButton("关闭项目")
        self.close_btn.clicked.connect(self.close_project)
        self.close_btn.setEnabled(False)
        header.addWidget(self.new_btn)
        header.addWidget(self.open_btn)
        header.addWidget(self.close_btn)
        root.addLayout(header)

        # Tabs
        self.tabs = QTabWidget()
        self.encrypt_panel = EncryptPanel(self.get_project, self.log)
        self.decrypt_panel = DecryptPanel(self.get_project, self.log)
        self.history_panel = HistoryPanel(self.get_project)
        self.tabs.addTab(self.encrypt_panel, "脱敏")
        self.tabs.addTab(self.decrypt_panel, "还原")
        self.tabs.addTab(self.history_panel, "历史")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.setEnabled(False)
        root.addWidget(self.tabs, 1)

        # Footer log
        log_box = QGroupBox("日志")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(100)
        log_layout.addWidget(self.log_view)
        root.addWidget(log_box)

    # ---------- project lifecycle ----------

    def get_project(self) -> Optional[Project]:
        return self._project

    def new_project(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "新建项目（保存到 .keyfile）", "myproject.keyfile", KEYFILE_FILTER
        )
        if not path:
            return
        if not path.endswith(".keyfile"):
            path += ".keyfile"
        name, ok = QInputDialog.getText(self, "项目名称", "为这个项目起个名字：")
        if not ok or not name.strip():
            return
        # Loop until user supplies a password meeting the minimum bar, or cancels.
        while True:
            password = self._ask_password(
                "设置密码", "为密钥文件设置密码（至少 8 位，建议含字母+数字）：\n密码丢失无法找回。"
            )
            if password is None:
                return  # cancelled
            if len(password) < 8:
                QMessageBox.warning(
                    self,
                    "密码太短",
                    "密码至少需要 8 位。\n\n"
                    "弱密码会让攻击者拿到 keyfile 后能快速离线爆破出你的映射表。",
                )
                continue
            break
        confirm = self._ask_password("确认密码", "再输一次刚才的密码：")
        if password != confirm:
            QMessageBox.warning(self, "密码不一致", "两次输入的密码不一致。")
            return
        try:
            self._project = Project.create(path, name=name.strip(), password=password)
        except Exception as e:
            QMessageBox.critical(self, "创建失败", str(e))
            return
        self._on_project_loaded()

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开项目", "", KEYFILE_FILTER)
        if not path:
            return
        password = self._ask_password("输入密码", "请输入密钥文件密码：")
        if password is None:
            return
        try:
            self._project = Project.open(path, password)
        except WrongPassword:
            QMessageBox.warning(self, "密码错误", "密码不正确。")
            return
        except KeyfileError as e:
            QMessageBox.critical(self, "无法打开", str(e))
            return
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "无法打开", f"{type(e).__name__}: {e}")
            return
        self._on_project_loaded()

    def close_project(self):
        self._project = None
        self.project_label.setText("当前项目：（未打开）")
        self.tabs.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.encrypt_panel.encrypt_btn.setEnabled(False)
        self.decrypt_panel.decrypt_btn.setEnabled(False)
        self.log("已关闭项目。")

    def _on_project_loaded(self):
        proj = self._project
        self.project_label.setText(
            f"当前项目：{proj.name}    （{os.path.basename(proj.keyfile_path())}）"
        )
        self.tabs.setEnabled(True)
        self.close_btn.setEnabled(True)
        # Re-enable actions if files already picked
        if self.encrypt_panel._input_path:
            self.encrypt_panel.encrypt_btn.setEnabled(True)
        if self.decrypt_panel._input_path:
            self.decrypt_panel.decrypt_btn.setEnabled(True)
        self.history_panel.refresh()
        self.log(f"已打开项目：{proj.name}（包含 {sum(proj.tokenizer.stats().values())} 个 token）")

    def _on_tab_changed(self, idx: int):
        if self.tabs.widget(idx) is self.history_panel:
            self.history_panel.refresh()

    # ---------- helpers ----------

    def _ask_password(self, title: str, prompt: str) -> Optional[str]:
        text, ok = QInputDialog.getText(
            self, title, prompt, QLineEdit.EchoMode.Password
        )
        if not ok:
            return None
        return text

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}] {msg}")


def run():
    import sys
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
