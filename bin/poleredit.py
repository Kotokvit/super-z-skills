#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PolerEdit — AI-Augmented Text Editor with integrated POLER[n] engine.
Author: Antigravity + Super Z, 2026-07-21
"""

import sys
import os
import re
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor, QTextCharFormat, QColor, QSyntaxHighlighter
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPlainTextEdit, QTextEdit,
    QVBoxLayout, QHBoxLayout, QSplitter, QComboBox, QLineEdit,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QFileDialog,
    QStatusBar, QMenuBar, QMessageBox, QGroupBox, QFormLayout
)

# Insert sandbox path to load poler_enhanced
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == "bin" else SCRIPT_DIR
sys.path.insert(0, str(PROJECT_ROOT / "skills" / "_shared" / "sandbox"))

try:
    from poler_enhanced import PolerAnalyzer, THEMES, EMOTIONAL_MARKERS
except ImportError:
    # Fallback if imported path differs
    sys.path.insert(0, str(PROJECT_ROOT / "skills" / "_shared"))
    try:
        from sandbox.poler_enhanced import PolerAnalyzer, THEMES, EMOTIONAL_MARKERS
    except ImportError:
        # Minimal mockup of poler if everything fails
        class PolerAnalyzer:
            def __init__(self, **kwargs): pass
            def analyze_text(self, text, kw): return {"windows": [], "top_by_epsilon": [], "summary": {}}
        THEMES = {"mock": ["test"]}
        EMOTIONAL_MARKERS = {}

class PolerHighlighter(QSyntaxHighlighter):
    """Highlighter that colors key resonant words in the text."""
    def __init__(self, parent, keyword=""):
        super().__init__(parent)
        self.keyword = keyword
        self.emotional_words = set(EMOTIONAL_MARKERS)

    def set_keyword(self, keyword):
        self.keyword = keyword
        self.rehighlight()

    def highlightBlock(self, text):
        if not text:
            return
        
        # Highlight keyword (neon green/yellow)
        if self.keyword:
            kw_format = QTextCharFormat()
            kw_format.setBackground(QColor("#4ade80"))
            kw_format.setForeground(QColor("#000000"))
            kw_format.setFontWeight(QFont.Weight.Bold)
            
            pattern = re.compile(re.escape(self.keyword), re.IGNORECASE)
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), kw_format)

        # Highlight emotional markers (subtle purple)
        emo_format = QTextCharFormat()
        emo_format.setForeground(QColor("#c084fc"))
        emo_format.setFontUnderline(True)
        
        words_pattern = re.compile(r'\b\w+\b', re.UNICODE)
        for match in words_pattern.finditer(text):
            word = match.group(0).lower()
            if word in self.emotional_words:
                # Avoid overwriting keyword highlight
                if self.keyword and self.keyword.lower() in word:
                    continue
                self.setFormat(match.start(), match.end() - match.start(), emo_format)

class PolerEdit(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_file = None
        self.analyzer = PolerAnalyzer(window=2000, phi=0.85, top=5)
        self.init_ui()
        
        # Setup debounced typing analysis (wait 500ms after user stops typing)
        self.analysis_timer = QTimer()
        self.analysis_timer.setSingleShot(True)
        self.analysis_timer.timeout.connect(self.run_analysis)
        self.editor.textChanged.connect(self.on_text_changed)

    def init_ui(self):
        self.setWindowTitle("PolerEdit v1.0 — Project Eteria")
        self.resize(1200, 800)
        self.setup_styling()

        # Menu
        self.setup_menu()

        # Splitter Layout
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # Left Panel (Editor)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(10, 10, 10, 10)

        self.editor = QPlainTextEdit()
        # Set code font
        font = QFont("Fira Code", 12)
        if font.bold(): # falls back if not found
            font = QFont("Courier New", 12)
        self.editor.setFont(font)
        left_layout.addWidget(self.editor)
        
        # Highlighter
        self.highlighter = PolerHighlighter(self.editor.document())

        splitter.addWidget(left_widget)

        # Right Panel (POLER Dashboard)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 10, 10, 10)

        # 1. Config Group
        config_group = QGroupBox("POLER Controls")
        config_layout = QFormLayout(config_group)
        
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("None (Custom Keyword)")
        for tname in THEMES.keys():
            self.theme_combo.addItem(tname.capitalize())
        self.theme_combo.currentIndexChanged.connect(self.on_theme_changed)
        config_layout.addRow("Theme:", self.theme_combo)

        self.keyword_input = QLineEdit("сфер")
        self.keyword_input.textChanged.connect(self.on_keyword_changed)
        config_layout.addRow("Keyword:", self.keyword_input)
        
        right_layout.addWidget(config_group)

        # 2. Stats Group
        self.stats_group = QGroupBox("Resonance Summary")
        stats_layout = QVBoxLayout(self.stats_group)
        self.lbl_stats = QLabel("Total Epsilon: 0.00\nAverage Resonance: 0.00\nTotal Windows: 0")
        stats_layout.addWidget(self.lbl_stats)
        right_layout.addWidget(self.stats_group)

        # 3. Top Windows List
        list_group = QGroupBox("Top Resonant Windows")
        list_layout = QVBoxLayout(list_group)
        self.window_list = QListWidget()
        self.window_list.itemClicked.connect(self.on_list_item_clicked)
        list_layout.addWidget(self.window_list)
        right_layout.addWidget(list_group)

        splitter.addWidget(right_widget)
        
        # Splitter ratio: 70% editor, 30% panel
        splitter.setSizes([840, 360])

        # Status Bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")

    def setup_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        act_open = file_menu.addAction("Open...")
        act_open.triggered.connect(self.file_open)
        act_open.setShortcut("Ctrl+O")

        act_save = file_menu.addAction("Save")
        act_save.triggered.connect(self.file_save)
        act_save.setShortcut("Ctrl+S")

        act_save_as = file_menu.addAction("Save As...")
        act_save_as.triggered.connect(self.file_save_as)

        file_menu.addSeparator()
        act_exit = file_menu.addAction("Exit")
        act_exit.triggered.connect(self.close)

    def setup_styling(self):
        # Elegant Dark Cyberpunk Theme (Eteria style)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f172a;
            }
            QWidget {
                background-color: #0f172a;
                color: #f1f5f9;
                font-family: "Segoe UI", "Helvetica Neue", sans-serif;
            }
            QPlainTextEdit {
                background-color: #1e293b;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 10px;
                line-height: 1.5;
            }
            QGroupBox {
                border: 2px solid #334155;
                border-radius: 8px;
                margin-top: 15px;
                padding-top: 15px;
                font-weight: bold;
                color: #c084fc; /* Neon Purple */
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            QLineEdit, QComboBox {
                background-color: #1e293b;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 6px;
                color: #f8fafc;
            }
            QComboBox::drop-down {
                border: none;
            }
            QListWidget {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 5px;
            }
            QListWidget::item {
                border-bottom: 1px solid #334155;
                padding: 10px;
            }
            QListWidget::item:hover {
                background-color: #334155;
            }
            QListWidget::item:selected {
                background-color: #581c87;
                color: #f3e8ff;
            }
            QMenuBar {
                background-color: #0f172a;
                border-bottom: 1px solid #1e293b;
            }
            QMenuBar::item:selected {
                background-color: #334155;
            }
            QStatusBar {
                background-color: #020617;
                color: #94a3b8;
            }
        """)

    def file_open(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Open File", "", "Text Files (*.txt *.md *.py *.json);;All Files (*)")
        if fname:
            try:
                content = Path(fname).read_text(encoding="utf-8")
                self.editor.setPlainText(content)
                self.current_file = fname
                self.status.showMessage(f"Opened: {fname}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not read file: {e}")

    def file_save(self):
        if not self.current_file:
            self.file_save_as()
        else:
            try:
                content = self.editor.toPlainText()
                Path(self.current_file).write_text(content, encoding="utf-8")
                self.status.showMessage(f"Saved: {self.current_file}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save file: {e}")

    def file_save_as(self):
        fname, _ = QFileDialog.getSaveFileName(self, "Save File As", "", "Text Files (*.txt *.md *.py *.json);;All Files (*)")
        if fname:
            self.current_file = fname
            self.file_save()

    def on_text_changed(self):
        # Debounce the analysis run (reset timer on every keypress)
        self.analysis_timer.start(500)

    def on_keyword_changed(self, text):
        self.highlighter.set_keyword(text)
        self.analysis_timer.start(500)

    def on_theme_changed(self, index):
        if index == 0:
            self.keyword_input.setEnabled(True)
        else:
            tname = self.theme_combo.itemText(index).lower()
            keywords = THEMES.get(tname, [])
            if keywords:
                # Set the first keyword of the theme
                self.keyword_input.setText(keywords[0])
                self.keyword_input.setEnabled(False)
                self.status.showMessage(f"Applied theme '{tname}' with keywords: {', '.join(keywords)}")

    def run_analysis(self):
        text = self.editor.toPlainText()
        keyword = self.keyword_input.text().strip()
        
        if not text.strip() or not keyword:
            self.lbl_stats.setText("Total Epsilon: 0.00\nAverage Resonance: 0.00\nTotal Windows: 0")
            self.window_list.clear()
            return

        self.status.showMessage("Running POLER analysis...")
        
        try:
            result = self.analyzer.analyze_text(text, keyword)
            summary = result.get("summary", {})
            top_eps = result.get("top_by_epsilon", [])
            
            # Update stats label
            tot_eps = summary.get("total_epsilon", 0)
            avg_res = summary.get("avg_resonance", 0)
            tot_win = summary.get("total_windows", 0)
            self.lbl_stats.setText(
                f"Total Epsilon: {tot_eps:.2f}\n"
                f"Average Resonance: {avg_res:.2f}\n"
                f"Total Windows: {tot_win}"
            )
            
            # Update window list
            self.window_list.clear()
            for w in top_eps:
                w_idx = w.get("index", 0)
                w_pos = w.get("position", 0)
                w_eps = w.get("epsilon", 0)
                w_res = w.get("resonance", 0)
                w_text = w.get("cleaned_text", "")
                
                # Preview snippet
                preview = w_text[:120].replace('\n', ' ') + "..." if len(w_text) > 120 else w_text
                
                item = QListWidgetItem(
                    f"Win #{w_idx} (pos: {w_pos}) | ε: {w_eps:.1f}  R: {w_res:.1f}\n"
                    f"Snippet: {preview}"
                )
                # Store position so we can scroll to it
                item.setData(Qt.ItemDataRole.UserRole, w_pos)
                self.window_list.addItem(item)
                
            self.status.showMessage("POLER Analysis Complete", 2000)
        except Exception as e:
            self.status.showMessage(f"POLER Error: {e}", 5000)

    def on_list_item_clicked(self, item):
        pos = item.data(Qt.ItemDataRole.UserRole)
        if pos is not None:
            # Scroll editor to the character position
            cursor = self.editor.textCursor()
            cursor.setPosition(pos)
            self.editor.setTextCursor(cursor)
            self.editor.setFocus()

def main():
    app = QApplication(sys.argv)
    editor = PolerEdit()
    editor.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
