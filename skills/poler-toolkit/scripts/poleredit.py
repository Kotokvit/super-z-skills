#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PolerEdit v2.0 — Semantic Navigation Editor
=============================================
GUI для человека: редактор + POLER[Ψ] v3.0 Core
  - Динамические темы (не фиксированные!)
  - Авто-обнаружение доменов из текста
  - Семантические вены навигации
  - Ручной режим (пользователь задаёт домены)

Архитектура:
  PolerEdit = интерфейс для ЧЕЛОВЕКА
  POLER Core = понимание (ядро смысла)
  super_z_core = маршрутизация (нервная система)

Python = прототип → Rust/Zig/C (Tauri/egui)
"""

import sys
import os
import json
import re
import time
from pathlib import Path
from collections import Counter

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize
from PyQt6.QtGui import (
    QFont, QTextCursor, QTextCharFormat, QColor,
    QSyntaxHighlighter, QIcon, QAction, QKeySequence
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPlainTextEdit, QTextEdit,
    QVBoxLayout, QHBoxLayout, QSplitter, QComboBox, QLineEdit,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QFileDialog,
    QStatusBar, QMenuBar, QMessageBox, QGroupBox, QFormLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QToolBar, QInputDialog, QCheckBox, QSpinBox, QDoubleSpinBox,
)

# ═══════════════════════════════════════════════════════════════════════
# ИМПОРТ POLER[Ψ] v3.0 CORE
# ═══════════════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent

# Пробуем несколько путей
_POLEDIT_PATHS = [
    SCRIPT_DIR / "poler_enhanced_v3.py",
    SCRIPT_DIR.parent / "scripts" / "poler_enhanced_v3.py",
    SCRIPT_DIR.parent / "poler_enhanced_v3.py",
    Path("/home/z/my-project/scripts/poler_enhanced_v3.py"),
]

_poler_module = None
for _p in _POLEDIT_PATHS:
    if _p.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("poler_enhanced_v3", str(_p))
        _poler_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_poler_module)
        break

if _poler_module is None:
    print("FATAL: POLER[Ψ] v3.0 Core не найден!")
    print("Ожидалось в одном из путей:")
    for p in _POLEDIT_PATHS:
        print(f"  {p}")
    sys.exit(1)

# Импортируем нужные классы/функции
PolerAnalyzer = _poler_module.PolerAnalyzer
build_veins = _poler_module.build_veins
auto_discover_themes = _poler_module.auto_discover_themes
auto_extract_keywords = _poler_module.auto_extract_keywords
detect_domains = _poler_module.detect_domains
DOMAIN_SEEDS = _poler_module.DOMAIN_SEEDS
EMOTIONAL_MARKERS = _poler_module.EMOTIONAL_MARKERS


# ═══════════════════════════════════════════════════════════════════════
# ПОДСВЕТКА СИНТАКСИСА (семантическая)
# ═══════════════════════════════════════════════════════════════════════

class SemanticHighlighter(QSyntaxHighlighter):
    """Подсветка на основе POLER: ключевые слова + эмоциональные маркеры + домены."""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.keywords = []         # Активные ключевые слова (из вен)
        self.emotional_words = set(EMOTIONAL_MARKERS)
        self.domain_keywords = {}  # {domain: [keywords]}
    
    def set_keywords(self, keywords: list):
        self.keywords = [k.lower() for k in keywords]
        self.rehighlight()
    
    def set_domain_keywords(self, domain_map: dict):
        self.domain_keywords = {}
        for domain, kws in domain_map.items():
            for kw in kws:
                self.domain_keywords[kw.lower()] = domain
        self.rehighlight()
    
    def highlightBlock(self, text):
        if not text:
            return
        
        # 1. Подсветка ключевых слов из вен (неоновый зелёный)
        kw_format = QTextCharFormat()
        kw_format.setBackground(QColor("#065f46"))
        kw_format.setForeground(QColor("#6ee7b7"))
        kw_format.setFontWeight(QFont.Weight.Bold)
        
        for kw in self.keywords:
            if not kw:
                continue
            pattern = re.compile(re.escape(kw), re.IGNORECASE)
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), kw_format)
        
        # 2. Подсветка доменных ключевых слов (разные цвета по домену)
        domain_colors = {
            'physics': ('#1e3a5f', '#93c5fd'),
            'mathematics': ('#3b1f6e', '#c4b5fd'),
            'computer_science': ('#134e4a', '#5eead4'),
            'economics': ('#422006', '#fcd34d'),
            'biology': ('#14532d', '#86efac'),
            'chemistry': ('#431407', '#fdba74'),
            'medicine': ('#4c1d95', '#c4b5fd'),
            'linguistics': ('#713f12', '#fde68a'),
            'history': ('#7c2d12', '#fdba74'),
            'law': ('#1e3a5f', '#93c5fd'),
        }
        
        for kw, domain in self.domain_keywords.items():
            if kw in self.keywords:
                continue  # Уже подсвечено как ключевое
            bg, fg = domain_colors.get(domain, ('#1e293b', '#94a3b8'))
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(bg))
            fmt.setForeground(QColor(fg))
            fmt.setFontWeight(QFont.Weight.DemiBold)
            pattern = re.compile(re.escape(kw), re.IGNORECASE)
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)
        
        # 3. Эмоциональные маркеры (тусклый пурпурный + подчёркивание)
        emo_format = QTextCharFormat()
        emo_format.setForeground(QColor("#a78bfa"))
        emo_format.setFontUnderline(True)
        
        words_pattern = re.compile(r'\b\w+\b', re.UNICODE)
        for match in words_pattern.finditer(text):
            word = match.group(0).lower()
            if word in self.emotional_words:
                if word not in self.keywords:
                    self.setFormat(match.start(), match.end() - match.start(), emo_format)


# ═══════════════════════════════════════════════════════════════════════
# ФОНОВЫЙ АНАЛИЗАТОР (чтобы не блокировать UI)
# ═══════════════════════════════════════════════════════════════════════

class AnalysisWorker(QThread):
    """Фоновый поток для POLER анализа."""
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    
    def __init__(self, text, mode='veins', custom_seeds=None, keywords=None):
        super().__init__()
        self.text = text
        self.mode = mode
        self.custom_seeds = custom_seeds
        self.keywords = keywords
    
    def run(self):
        try:
            if self.mode == 'veins':
                result = build_veins(
                    self.text,
                    keywords=self.keywords,
                    custom_seeds=self.custom_seeds,
                    source_file='<editor>',
                )
            elif self.mode == 'understand':
                result = auto_discover_themes(self.text, custom_seeds=self.custom_seeds)
            elif self.mode == 'keywords':
                result = {'keywords': auto_extract_keywords(self.text, top_n=30)}
            else:
                result = build_veins(self.text, custom_seeds=self.custom_seeds, source_file='<editor>')
            
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════
# ГЛАВНОЕ ОКНО
# ═══════════════════════════════════════════════════════════════════════

class PolerEdit(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_file = None
        self.analyzer = PolerAnalyzer(window=3000, phi=0.85, top=10)
        self.current_result = None
        self.worker = None
        self.custom_seeds = None
        
        self.init_ui()
        
        # Debounce: ждать 700ms после последнего нажатия
        self.analysis_timer = QTimer()
        self.analysis_timer.setSingleShot(True)
        self.analysis_timer.timeout.connect(self.run_auto_analysis)
        self.editor.textChanged.connect(self.on_text_changed)
    
    def init_ui(self):
        self.setWindowTitle("PolerEdit v2.0 — POLER[Ψ] Semantic Editor")
        self.resize(1400, 900)
        self.setup_styling()
        self.setup_menu()
        self.setup_toolbar()
        
        # Главный сплиттер
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(main_splitter)
        
        # ─── Левая панель: Редактор ───
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(6, 6, 6, 6)
        
        self.editor = QPlainTextEdit()
        font = QFont()
        font.setFamilies(["Fira Code", "JetBrains Mono", "Consolas", "Courier New"])
        font.setPointSize(12)
        self.editor.setFont(font)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        left_layout.addWidget(self.editor)
        
        self.highlighter = SemanticHighlighter(self.editor.document())
        
        main_splitter.addWidget(left_widget)
        
        # ─── Правая панель: POLER Dashboard ───
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 6, 6, 6)
        
        # Табы для разных режимов
        self.tabs = QTabWidget()
        right_layout.addWidget(self.tabs)
        
        # --- Таб 1: Обзор (домены + авто-анализ) ---
        overview_widget = QWidget()
        overview_layout = QVBoxLayout(overview_widget)
        
        # Кнопка авто-анализа
        auto_btn_layout = QHBoxLayout()
        self.btn_auto_analyze = QPushButton("🔍 Auto-Analyze")
        self.btn_auto_analyze.clicked.connect(self.run_auto_analysis)
        self.btn_auto_analyze.setStyleSheet(
            "QPushButton { background-color: #7c3aed; color: white; "
            "border-radius: 6px; padding: 10px; font-weight: bold; font-size: 14px; }"
            "QPushButton:hover { background-color: #6d28d9; }"
        )
        auto_btn_layout.addWidget(self.btn_auto_analyze)
        
        self.chk_auto_live = QCheckBox("Live")
        self.chk_auto_live.setChecked(True)
        self.chk_auto_live.setToolTip("Auto-analyze on text change")
        auto_btn_layout.addWidget(self.chk_auto_live)
        overview_layout.addLayout(auto_btn_layout)
        
        # Домены
        self.domains_group = QGroupBox("Detected Domains")
        domains_layout = QVBoxLayout(self.domains_group)
        self.domains_table = QTableWidget(0, 3)
        self.domains_table.setHorizontalHeaderLabels(["Domain", "Confidence", "Markers"])
        self.domains_table.horizontalHeader().setStretchLastSection(True)
        self.domains_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.domains_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.domains_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.domains_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        domains_layout.addWidget(self.domains_table)
        overview_layout.addWidget(self.domains_group)
        
        # Ключевые слова
        self.kw_group = QGroupBox("Auto-Extracted Keywords")
        kw_layout = QVBoxLayout(self.kw_group)
        self.kw_table = QTableWidget(0, 4)
        self.kw_table.setHorizontalHeaderLabels(["Word", "Freq", "Rarity", "Score"])
        self.kw_table.horizontalHeader().setStretchLastSection(True)
        self.kw_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 4):
            self.kw_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.kw_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.kw_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.kw_table.itemClicked = self.on_kw_clicked
        kw_layout.addWidget(self.kw_table)
        overview_layout.addWidget(self.kw_group)
        
        self.tabs.addTab(overview_widget, "Overview")
        
        # --- Таб 2: Вены (семантическая навигация) ---
        veins_widget = QWidget()
        veins_layout = QVBoxLayout(veins_widget)
        
        # Контроли для вен
        veins_ctrl = QHBoxLayout()
        self.btn_build_veins = QPushButton("🧭 Build Veins")
        self.btn_build_veins.clicked.connect(self.run_veins_analysis)
        self.btn_build_veins.setStyleSheet(
            "QPushButton { background-color: #0ea5e9; color: white; "
            "border-radius: 6px; padding: 8px; font-weight: bold; }"
            "QPushButton:hover { background-color: #0284c7; }"
        )
        veins_ctrl.addWidget(self.btn_build_veins)
        
        self.keyword_filter = QLineEdit()
        self.keyword_filter.setPlaceholderText("Filter keywords...")
        self.keyword_filter.textChanged.connect(self.filter_veins)
        veins_ctrl.addWidget(self.keyword_filter)
        veins_layout.addLayout(veins_ctrl)
        
        # Список вен
        self.veins_list = QListWidget()
        self.veins_list.itemClicked.connect(self.on_vein_clicked)
        veins_layout.addWidget(self.veins_list)
        
        # Информация о вене
        self.vein_info = QLabel("Click a vein to navigate")
        self.vein_info.setWordWrap(True)
        self.vein_info.setStyleSheet("color: #94a3b8; padding: 8px;")
        veins_layout.addWidget(self.vein_info)
        
        self.tabs.addTab(veins_widget, "Veins")
        
        # --- Таб 3: Классический анализ (single keyword) ---
        classic_widget = QWidget()
        classic_layout = QVBoxLayout(classic_widget)
        
        ctrl_layout = QHBoxLayout()
        self.keyword_input = QLineEdit("quantum")
        self.keyword_input.setPlaceholderText("Keyword...")
        self.keyword_input.textChanged.connect(self.on_keyword_changed)
        ctrl_layout.addWidget(QLabel("Keyword:"))
        ctrl_layout.addWidget(self.keyword_input)
        
        self.btn_classic = QPushButton("Analyze")
        self.btn_classic.clicked.connect(self.run_classic_analysis)
        ctrl_layout.addWidget(self.btn_classic)
        classic_layout.addLayout(ctrl_layout)
        
        # Результаты классического анализа
        self.classic_stats = QLabel("Total Epsilon: 0\nWindows: 0\nPeak ε: 0")
        self.classic_stats.setStyleSheet("padding: 10px; font-size: 13px;")
        classic_layout.addWidget(self.classic_stats)
        
        self.classic_list = QListWidget()
        self.classic_list.itemClicked.connect(self.on_classic_item_clicked)
        classic_layout.addWidget(self.classic_list)
        
        self.tabs.addTab(classic_widget, "Classic")
        
        # --- Таб 4: Настройки (ручной режим) ---
        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        
        # Custom seeds
        seeds_group = QGroupBox("Custom Domain Seeds (Manual Mode)")
        seeds_layout = QVBoxLayout(seeds_group)
        
        self.seeds_editor = QPlainTextEdit()
        self.seeds_editor.setPlaceholderText(
            '{\n  "my_domain": ["keyword1", "keyword2", "keyword3"],\n  '
            '"another_domain": ["marker1", "marker2"]\n}'
        )
        self.seeds_editor.setMaximumHeight(200)
        seeds_layout.addWidget(self.seeds_editor)
        
        seeds_btn_layout = QHBoxLayout()
        self.btn_load_seeds = QPushButton("Load Custom Seeds")
        self.btn_load_seeds.clicked.connect(self.load_custom_seeds)
        self.btn_load_seeds.setStyleSheet(
            "QPushButton { background-color: #f59e0b; color: black; "
            "border-radius: 6px; padding: 8px; font-weight: bold; }"
        )
        seeds_btn_layout.addWidget(self.btn_load_seeds)
        
        self.btn_clear_seeds = QPushButton("Clear Seeds")
        self.btn_clear_seeds.clicked.connect(self.clear_custom_seeds)
        seeds_btn_layout.addWidget(self.btn_clear_seeds)
        seeds_layout.addLayout(seeds_btn_layout)
        
        settings_layout.addWidget(seeds_group)
        
        # POLER параметры
        params_group = QGroupBox("POLER Parameters")
        params_layout = QFormLayout(params_group)
        
        self.spin_window = QSpinBox()
        self.spin_window.setRange(500, 50000)
        self.spin_window.setValue(3000)
        self.spin_window.setSingleStep(500)
        params_layout.addRow("Window Size:", self.spin_window)
        
        self.spin_phi = QDoubleSpinBox()
        self.spin_phi.setRange(0.1, 0.99)
        self.spin_phi.setValue(0.85)
        self.spin_phi.setSingleStep(0.05)
        params_layout.addRow("Phi Decay:", self.spin_phi)
        
        self.spin_kappa = QDoubleSpinBox()
        self.spin_kappa.setRange(0.1, 5.0)
        self.spin_kappa.setValue(1.0)
        self.spin_kappa.setSingleStep(0.1)
        params_layout.addRow("Kappa:", self.spin_kappa)
        
        self.spin_top = QSpinBox()
        self.spin_top.setRange(3, 50)
        self.spin_top.setValue(10)
        params_layout.addRow("Top N:", self.spin_top)
        
        settings_layout.addWidget(params_group)
        settings_layout.addStretch()
        
        self.tabs.addTab(settings_widget, "Settings")
        
        main_splitter.addWidget(right_widget)
        main_splitter.setSizes([980, 420])
        
        # Статус бар
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready — POLER[Ψ] v3.0")
    
    # ─── Menu ───
    
    def setup_menu(self):
        menubar = self.menuBar()
        
        file_menu = menubar.addMenu("File")
        
        act_open = file_menu.addAction("Open...")
        act_open.triggered.connect(self.file_open)
        act_open.setShortcut(QKeySequence("Ctrl+O"))
        
        act_save = file_menu.addAction("Save")
        act_save.triggered.connect(self.file_save)
        act_save.setShortcut(QKeySequence("Ctrl+S"))
        
        act_save_as = file_menu.addAction("Save As...")
        act_save.triggered.connect(self.file_save_as)
        
        file_menu.addSeparator()
        act_exit = file_menu.addAction("Exit")
        act_exit.triggered.connect(self.close)
        
        # Analysis menu
        analysis_menu = menubar.addMenu("Analysis")
        
        act_auto = analysis_menu.addAction("Auto-Analyze")
        act_auto.triggered.connect(self.run_auto_analysis)
        act_auto.setShortcut(QKeySequence("Ctrl+Return"))
        
        act_veins = analysis_menu.addAction("Build Veins")
        act_veins.triggered.connect(self.run_veins_analysis)
        act_veins.setShortcut(QKeySequence("Ctrl+Shift+V"))
        
        analysis_menu.addSeparator()
        act_export = analysis_menu.addAction("Export Results as JSON...")
        act_export.triggered.connect(self.export_results)
    
    def setup_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        toolbar.addAction("📂 Open", self.file_open)
        toolbar.addAction("💾 Save", self.file_save)
        toolbar.addSeparator()
        toolbar.addAction("🔍 Analyze", self.run_auto_analysis)
        toolbar.addAction("🧭 Veins", self.run_veins_analysis)
    
    # ─── Styling ───
    
    def setup_styling(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #0f172a; }
            QWidget { background-color: #0f172a; color: #f1f5f9; font-family: "Segoe UI", sans-serif; }
            QPlainTextEdit {
                background-color: #1e293b; color: #e2e8f0;
                border: 1px solid #334155; border-radius: 8px; padding: 10px;
            }
            QGroupBox {
                border: 2px solid #334155; border-radius: 8px;
                margin-top: 15px; padding-top: 15px;
                font-weight: bold; color: #c084fc;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #1e293b; border: 1px solid #475569;
                border-radius: 4px; padding: 6px; color: #f8fafc;
            }
            QListWidget {
                background-color: #1e293b; border: 1px solid #334155;
                border-radius: 8px; padding: 5px;
            }
            QListWidget::item { border-bottom: 1px solid #334155; padding: 8px; }
            QListWidget::item:hover { background-color: #334155; }
            QListWidget::item:selected { background-color: #581c87; color: #f3e8ff; }
            QTableWidget {
                background-color: #1e293b; border: 1px solid #334155;
                border-radius: 8px; gridline-color: #334155;
            }
            QTableWidget::item { padding: 4px; }
            QHeaderView::section {
                background-color: #0f172a; color: #94a3b8;
                border: 1px solid #334155; padding: 4px; font-weight: bold;
            }
            QTabWidget::pane { border: 1px solid #334155; border-radius: 8px; }
            QTabBar::tab {
                background-color: #1e293b; color: #94a3b8;
                border: 1px solid #334155; padding: 8px 16px;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }
            QTabBar::tab:selected { background-color: #334155; color: #f1f5f9; }
            QMenuBar { background-color: #0f172a; border-bottom: 1px solid #1e293b; }
            QMenuBar::item:selected { background-color: #334155; }
            QStatusBar { background-color: #020617; color: #94a3b8; }
            QToolBar { background-color: #0f172a; border-bottom: 1px solid #1e293b; spacing: 6px; padding: 4px; }
            QCheckBox { color: #94a3b8; spacing: 6px; }
            QPushButton {
                background-color: #334155; color: #f1f5f9; border: 1px solid #475569;
                border-radius: 6px; padding: 6px 12px;
            }
            QPushButton:hover { background-color: #475569; }
        """)
    
    # ─── File Operations ───
    
    def file_open(self):
        fname, _ = QFileDialog.getOpenFileName(
            self, "Open File", "",
            "Text Files (*.txt *.md *.py *.json *.tex *.rst);;All Files (*)"
        )
        if fname:
            try:
                content = Path(fname).read_text(encoding='utf-8', errors='ignore')
                self.editor.setPlainText(content)
                self.current_file = fname
                self.status.showMessage(f"Opened: {fname}")
                self.run_auto_analysis()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not read file: {e}")
    
    def file_save(self):
        if not self.current_file:
            self.file_save_as()
        else:
            try:
                content = self.editor.toPlainText()
                Path(self.current_file).write_text(content, encoding='utf-8')
                self.status.showMessage(f"Saved: {self.current_file}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save: {e}")
    
    def file_save_as(self):
        fname, _ = QFileDialog.getSaveFileName(
            self, "Save File As", "",
            "Text Files (*.txt *.md *.py *.json);;All Files (*)"
        )
        if fname:
            self.current_file = fname
            self.file_save()
    
    def export_results(self):
        if not self.current_result:
            QMessageBox.information(self, "Info", "No analysis results to export. Run analysis first.")
            return
        fname, _ = QFileDialog.getSaveFileName(
            self, "Export Results", "", "JSON Files (*.json);;All Files (*)"
        )
        if fname:
            try:
                with open(fname, 'w', encoding='utf-8') as f:
                    json.dump(self.current_result, f, ensure_ascii=False, indent=2, default=str)
                self.status.showMessage(f"Exported: {fname}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Export failed: {e}")
    
    # ─── Custom Seeds (Manual Mode) ───
    
    def load_custom_seeds(self):
        text = self.seeds_editor.toPlainText().strip()
        if not text:
            return
        try:
            seeds = json.loads(text)
            if not isinstance(seeds, dict):
                raise ValueError("Must be a JSON object: {domain: [keywords]}")
            self.custom_seeds = seeds
            self.status.showMessage(f"Loaded {len(seeds)} custom domain(s): {', '.join(seeds.keys())}")
            self.run_auto_analysis()
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, "JSON Error", f"Invalid JSON: {e}")
        except ValueError as e:
            QMessageBox.warning(self, "Format Error", str(e))
    
    def clear_custom_seeds(self):
        self.custom_seeds = None
        self.seeds_editor.clear()
        self.status.showMessage("Custom seeds cleared")
        self.run_auto_analysis()
    
    # ─── Analysis ───
    
    def on_text_changed(self):
        if self.chk_auto_live.isChecked():
            self.analysis_timer.start(700)
    
    def on_keyword_changed(self, text):
        self.highlighter.set_keywords([text] if text else [])
    
    def _update_analyzer_params(self):
        """Обновляет параметры анализатора из Settings."""
        self.analyzer = PolerAnalyzer(
            window=self.spin_window.value(),
            phi=self.spin_phi.value(),
            kappa=self.spin_kappa.value(),
            top=self.spin_top.value(),
        )
    
    def run_auto_analysis(self):
        """Авто-анализ: домены + ключевые слова + вены."""
        text = self.editor.toPlainText()
        if not text.strip():
            return
        
        self._update_analyzer_params()
        self.status.showMessage("Analyzing...")
        
        # Запуск в фоне
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
        
        self.worker = AnalysisWorker(text, mode='veins', custom_seeds=self.custom_seeds)
        self.worker.finished.connect(self._on_auto_analysis_done)
        self.worker.error.connect(self._on_analysis_error)
        self.worker.start()
    
    def run_veins_analysis(self):
        """Построение вен (явный запрос)."""
        text = self.editor.toPlainText()
        if not text.strip():
            return
        
        self._update_analyzer_params()
        
        # Проверяем, есть ли ключевое слово в классическом табе
        kw = self.keyword_input.text().strip()
        keywords = [kw] if kw else None
        
        self.status.showMessage("Building semantic veins...")
        
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
        
        self.worker = AnalysisWorker(text, mode='veins', custom_seeds=self.custom_seeds, keywords=keywords)
        self.worker.finished.connect(self._on_auto_analysis_done)
        self.worker.error.connect(self._on_analysis_error)
        self.worker.start()
    
    def run_classic_analysis(self):
        """Классический single-keyword анализ."""
        text = self.editor.toPlainText()
        keyword = self.keyword_input.text().strip()
        
        if not text.strip() or not keyword:
            return
        
        self._update_analyzer_params()
        self.status.showMessage(f"Analyzing '{keyword}'...")
        
        try:
            result = self.analyzer.analyze_text(text, keyword)
            self._on_classic_done(result)
        except Exception as e:
            self._on_analysis_error(str(e))
    
    def _on_auto_analysis_done(self, result: dict):
        """Обработка результата авто-анализа / вен."""
        self.current_result = result
        
        # Обновляем Overview таб
        self._update_domains_table(result.get('domains', []))
        self._update_keywords_table(result.get('keywords', result.get('theme_map', {})))
        
        # Обновляем Veins таб
        self._update_veins_list(result.get('veins', []))
        
        # Обновляем подсветку
        veins = result.get('veins', [])
        if veins:
            keywords = [v['keyword'] for v in veins[:10]]
            self.highlighter.set_keywords(keywords)
            
            # Доменные ключевые слова
            theme_map = result.get('theme_map', {})
            self.highlighter.set_domain_keywords(theme_map)
        
        # Статус
        n_domains = len(result.get('domains', []))
        n_veins = len(veins)
        self.status.showMessage(
            f"Analysis complete: {n_domains} domain(s), {n_veins} vein(s)", 5000
        )
        
        # Переключаем на Overview
        self.tabs.setCurrentIndex(0)
    
    def _on_classic_done(self, result: dict):
        """Обработка результата классического анализа."""
        summary = result.get('summary', {})
        top_eps = result.get('top_by_epsilon', [])
        
        if summary:
            self.classic_stats.setText(
                f"Total Epsilon: {summary.get('total_epsilon', 0):,.1f}\n"
                f"Average Epsilon: {summary.get('avg_epsilon', 0):,.2f}\n"
                f"Peak Epsilon: {summary.get('peak_epsilon', 0):,.1f}\n"
                f"Windows: {summary.get('total_windows', 0)}\n"
                f"PII Filtered: {summary.get('total_pii', 0)}"
            )
        
        self.classic_list.clear()
        for w in top_eps:
            w_idx = w.get('index', 0)
            w_pos = w.get('position', 0)
            w_eps = w.get('epsilon', 0)
            w_res = w.get('resonance', 0)
            w_text = w.get('cleaned_text', '')
            
            preview = w_text[:100].replace('\n', ' ')
            if len(w_text) > 100:
                preview += "..."
            
            item = QListWidgetItem(
                f"#{w_idx} | pos:{w_pos} | ε:{w_eps:.0f} | R:{w_res:.0f}\n{preview}"
            )
            item.setData(Qt.ItemDataRole.UserRole, w_pos)
            self.classic_list.addItem(item)
        
        # Подсветка
        kw = result.get('keyword', '')
        if kw:
            self.highlighter.set_keywords([kw])
        
        self.status.showMessage(f"Classic analysis done: {len(top_eps)} windows", 3000)
    
    def _on_analysis_error(self, error_msg: str):
        self.status.showMessage(f"Error: {error_msg}", 10000)
    
    # ─── UI Updates ───
    
    def _update_domains_table(self, domains: list):
        self.domains_table.setRowCount(len(domains))
        for i, d in enumerate(domains):
            self.domains_table.setItem(i, 0, QTableWidgetItem(d.get('domain', '')))
            
            conf_item = QTableWidgetItem(f"{d.get('confidence', 0):.2f}")
            # Цвет по confidence
            conf = d.get('confidence', 0)
            if conf >= 0.7:
                conf_item.setForeground(QColor("#6ee7b7"))  # зелёный
            elif conf >= 0.4:
                conf_item.setForeground(QColor("#fcd34d"))  # жёлтый
            else:
                conf_item.setForeground(QColor("#f87171"))  # красный
            self.domains_table.setItem(i, 1, conf_item)
            
            markers = d.get('matched', [])
            self.domains_table.setItem(i, 2, QTableWidgetItem(', '.join(markers[:5])))
    
    def _update_keywords_table(self, data):
        """Обновляет таблицу ключевых слов."""
        if isinstance(data, list):
            # Это список из auto_extract_keywords
            keywords = data
        elif isinstance(data, dict):
            # Это theme_map — собираем все слова
            keywords = []
            for domain, kws in data.items():
                for kw in kws:
                    keywords.append({'word': kw, 'domain': domain})
            # Сортируем — доменные сначала
            keywords.sort(key=lambda x: x.get('domain', 'zzz'))
        else:
            keywords = []
        
        self.kw_table.setRowCount(min(len(keywords), 30))
        for i, kw in enumerate(keywords[:30]):
            if isinstance(kw, dict):
                self.kw_table.setItem(i, 0, QTableWidgetItem(kw.get('word', '')))
                self.kw_table.setItem(i, 1, QTableWidgetItem(str(kw.get('freq', ''))))
                self.kw_table.setItem(i, 2, QTableWidgetItem(f"{kw.get('rarity', 0):.2f}" if 'rarity' in kw else ''))
                self.kw_table.setItem(i, 3, QTableWidgetItem(f"{kw.get('score', 0):.2f}" if 'score' in kw else kw.get('domain', '')))
    
    def _update_veins_list(self, veins: list):
        self.veins_list.clear()
        for v in veins:
            keyword = v.get('keyword', '?')
            domain = v.get('domain', '?')
            eps = v.get('epsilon_peak', 0)
            res = v.get('resonance_integral', 0)
            n_pos = len(v.get('positions', []))
            conf = v.get('confidence', 0)
            
            # Цвет по домену
            domain_icons = {
                'physics': '⚛', 'mathematics': '∑', 'computer_science': '⟨/⟩',
                'economics': '💰', 'biology': '🧬', 'chemistry': '⚗',
                'medicine': '⚕', 'linguistics': '📝', 'history': '📜', 'law': '⚖',
            }
            icon = domain_icons.get(domain, '•')
            
            item = QListWidgetItem(
                f"{icon} {keyword} [{domain}] ε={eps:,.0f} R={res:,.0f} "
                f"({n_pos} pos, conf={conf:.2f})"
            )
            item.setData(Qt.ItemDataRole.UserRole, v)
            self.veins_list.addItem(item)
    
    def filter_veins(self, text):
        """Фильтр вен по ключевому слову."""
        for i in range(self.veins_list.count()):
            item = self.veins_list.item(i)
            item.setHidden(text.lower() not in item.text().lower())
    
    # ─── Navigation ───
    
    def on_vein_clicked(self, item):
        """Клик на вену — навигация к позиции + подсветка."""
        vein = item.data(Qt.ItemDataRole.UserRole)
        if not vein:
            return
        
        positions = vein.get('positions', [])
        keyword = vein.get('keyword', '')
        domain = vein.get('domain', '')
        eps = vein.get('epsilon_peak', 0)
        res = vein.get('resonance_integral', 0)
        fragment = vein.get('top_fragment', '')
        
        # Навигация к первой позиции
        if positions:
            cursor = self.editor.textCursor()
            cursor.setPosition(min(positions[0], len(self.editor.toPlainText()) - 1))
            self.editor.setTextCursor(cursor)
            self.editor.setFocus()
            self.editor.ensureCursorVisible()
        
        # Обновляем подсветку на эту вену
        self.highlighter.set_keywords([keyword])
        
        # Информация
        self.vein_info.setText(
            f"<b>{keyword}</b> [{domain}]<br>"
            f"ε peak: {eps:,.0f} | R integral: {res:,.0f} | "
            f"Positions: {len(positions)}<br>"
            f"<i>Fragment preview:</i> {fragment[:200]}..."
        )
        
        self.status.showMessage(f"Navigated to vein '{keyword}' at position {positions[0] if positions else '?'}")
    
    def on_kw_clicked(self, item):
        """Клик на ключевое слово — устанавливаем как фильтр."""
        if item:
            word = item.text()
            self.keyword_input.setText(word)
            self.highlighter.set_keywords([word])
    
    def on_classic_item_clicked(self, item):
        """Навигация к позиции классического окна."""
        pos = item.data(Qt.ItemDataRole.UserRole)
        if pos is not None:
            cursor = self.editor.textCursor()
            cursor.setPosition(min(pos, len(self.editor.toPlainText()) - 1))
            self.editor.setTextCursor(cursor)
            self.editor.setFocus()
            self.editor.ensureCursorVisible()


# ═══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PolerEdit")
    app.setApplicationVersion("2.0")
    
    editor = PolerEdit()
    editor.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
