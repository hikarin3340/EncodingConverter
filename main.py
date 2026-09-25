import sys
from pathlib import Path

from PySide2.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide2.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QFileDialog, QTableView, QPlainTextEdit,
    QCheckBox, QMessageBox, QSplitter, QLineEdit, QGroupBox, QFormLayout
)

from detector import detect_encoding, decode_with_encoding
from converter import (
    collect_all_files,
    convert_file_to_path,
    copy_file_to_path,
    output_encoding_for_mode,
)


class DropArea(QLabel):
    def __init__(self, on_drop):
        super().__init__("ここにファイル / フォルダをドラッグ＆ドロップ")
        self.on_drop = on_drop
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(90)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #888; border-radius: 8px; padding: 18px; }"
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.on_drop(paths)
        event.acceptProposedAction()


class FileTableModel(QAbstractTableModel):
    HEADERS = ["処理", "ファイル", "判定", "信頼度", "変換先", "状態"]

    def __init__(self):
        super().__init__()
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = ["enabled", "path", "detected", "confidence", "target", "status"][index.column()]

        if role == Qt.DisplayRole:
            if key == "enabled":
                return ""
            if key == "path":
                return str(row["path"])
            if key == "confidence":
                c = row.get("confidence")
                return f"{c:.0%}" if isinstance(c, float) else ""
            return str(row.get(key, ""))

        if role == Qt.CheckStateRole and index.column() == 0:
            return Qt.Checked if row["enabled"] else Qt.Unchecked

        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.column() == 0 and self.rows[index.row()].get("checkable", True):
            flags |= Qt.ItemIsUserCheckable | Qt.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.EditRole):
        if index.column() == 0 and role == Qt.CheckStateRole:
            self.rows[index.row()]["enabled"] = value == Qt.Checked
            self.dataChanged.emit(index, index, [Qt.CheckStateRole, Qt.DisplayRole])
            return True
        return False

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Encoding Converter")
        self.resize(1250, 800)

        self.current_roots = []

        self.mode = QComboBox()
        self.mode.addItems(["MATLAB", "Unix C", "Custom"])
        self.mode.currentTextChanged.connect(self.on_mode_changed)

        self.input_encoding = QComboBox()
        self.input_encoding.addItems([
            "Auto", "UTF-8", "UTF-8 BOM", "CP932", "Shift_JIS",
            "EUC-JP", "ISO-2022-JP"
        ])
        self.input_encoding.currentTextChanged.connect(self.refresh_preview)

        self.output_encoding = QComboBox()
        self.output_encoding.addItems([
            "UTF-8", "UTF-8 BOM", "CP932", "Shift_JIS",
            "EUC-JP", "ISO-2022-JP"
        ])
        self.output_encoding.currentTextChanged.connect(self.refresh_preview)

        self.custom_filter_enabled = QCheckBox("Customで拡張子を限定")
        self.custom_filter_enabled.setChecked(False)
        self.custom_filter_enabled.stateChanged.connect(self.reanalyze)

        self.extensions = QLineEdit(".c;.h;.cpp;.hpp;.m;.txt;.csv;.py;.sh")
        self.extensions.textChanged.connect(self.on_filter_changed)

        self.recursive = QCheckBox("サブフォルダも処理")
        self.recursive.setChecked(True)
        self.recursive.stateChanged.connect(self.reanalyze)

        self.overwrite = QCheckBox("元ファイルを上書き")
        self.overwrite.setChecked(False)
        self.overwrite.stateChanged.connect(self.update_backup_state)

        self.make_backup = QCheckBox("上書き時に .bak を作成")
        self.make_backup.setChecked(True)

        self.preserve_eol = QCheckBox("改行コードを維持")
        self.preserve_eol.setChecked(True)

        self.copy_nontext = QCheckBox("変換できないファイルも元のままコピー")
        self.copy_nontext.setChecked(True)

        self.drop_area = DropArea(self.add_paths)

        self.pick_files_btn = QPushButton("ファイルを追加")
        self.pick_files_btn.clicked.connect(self.pick_files)

        self.pick_folder_btn = QPushButton("フォルダを追加")
        self.pick_folder_btn.clicked.connect(self.pick_folder)

        self.clear_btn = QPushButton("一覧をクリア")
        self.clear_btn.clicked.connect(self.clear_all)

        self.reanalyze_btn = QPushButton("再判定")
        self.reanalyze_btn.clicked.connect(self.reanalyze)

        self.convert_btn = QPushButton("変換して保存")
        self.convert_btn.clicked.connect(self.convert_selected)

        self.model = FileTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setAlternatingRowColors(True)
        self.table.clicked.connect(self.preview_row)
        self.table.doubleClicked.connect(self.preview_row)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.before_preview = QPlainTextEdit()
        self.before_preview.setReadOnly(True)
        self.after_preview = QPlainTextEdit()
        self.after_preview.setReadOnly(True)

        self.candidate_label = QLabel("候補: -")
        self.candidate_label.setWordWrap(True)

        preview_split = QSplitter(Qt.Horizontal)
        preview_split.addWidget(self._wrap("読み取りプレビュー", self.before_preview))
        preview_split.addWidget(self._wrap("変換後プレビュー", self.after_preview))

        self.preview_group = QGroupBox("プレビュー")
        pv = QVBoxLayout(self.preview_group)
        pv.addWidget(self.candidate_label)
        pv.addWidget(preview_split)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)

        top = QHBoxLayout()
        top.addWidget(QLabel("モード:"))
        top.addWidget(self.mode)
        top.addSpacing(16)
        top.addWidget(QLabel("入力:"))
        top.addWidget(self.input_encoding)
        top.addSpacing(16)
        top.addWidget(QLabel("出力:"))
        top.addWidget(self.output_encoding)
        top.addStretch()

        options_box = QGroupBox("オプション")
        form = QFormLayout(options_box)

        filter_row = QHBoxLayout()
        filter_row.addWidget(self.custom_filter_enabled)
        filter_row.addWidget(self.extensions, 1)
        form.addRow("対象:", filter_row)

        opts1 = QHBoxLayout()
        opts1.addWidget(self.recursive)
        opts1.addWidget(self.preserve_eol)
        opts1.addWidget(self.copy_nontext)
        opts1.addStretch()
        form.addRow(opts1)

        opts2 = QHBoxLayout()
        opts2.addWidget(self.overwrite)
        opts2.addWidget(self.make_backup)
        opts2.addStretch()
        form.addRow(opts2)

        buttons = QHBoxLayout()
        buttons.addWidget(self.pick_files_btn)
        buttons.addWidget(self.pick_folder_btn)
        buttons.addWidget(self.clear_btn)
        buttons.addStretch()
        buttons.addWidget(self.reanalyze_btn)
        buttons.addWidget(self.convert_btn)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addWidget(options_box)
        layout.addWidget(self.drop_area)
        layout.addLayout(buttons)
        layout.addWidget(self.table, 4)
        layout.addWidget(self.preview_group, 4)
        layout.addWidget(QLabel("ログ"))
        layout.addWidget(self.log)
        self.setCentralWidget(central)

        self.on_mode_changed(self.mode.currentText())
        self.update_backup_state()

    def _wrap(self, title, widget):
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        lay.addWidget(widget)
        return box

    def on_mode_changed(self, mode):
        if mode == "MATLAB":
            self.input_encoding.setCurrentText("Auto")
            self.output_encoding.setCurrentText("CP932")
            self.input_encoding.setEnabled(False)
            self.output_encoding.setEnabled(False)
            self.custom_filter_enabled.setEnabled(False)
            self.extensions.setEnabled(False)
            self.preview_group.setVisible(False)
        elif mode == "Unix C":
            self.input_encoding.setCurrentText("Auto")
            self.output_encoding.setCurrentText("UTF-8")
            self.input_encoding.setEnabled(False)
            self.output_encoding.setEnabled(False)
            self.custom_filter_enabled.setEnabled(False)
            self.extensions.setEnabled(False)
            self.preview_group.setVisible(False)
        else:
            self.input_encoding.setEnabled(True)
            self.output_encoding.setEnabled(True)
            self.custom_filter_enabled.setEnabled(True)
            self.extensions.setEnabled(self.custom_filter_enabled.isChecked())
            self.preview_group.setVisible(True)

        self.reanalyze()

    def update_backup_state(self):
        self.make_backup.setEnabled(self.overwrite.isChecked())

    def on_filter_changed(self):
        if self.mode.currentText() == "Custom" and self.custom_filter_enabled.isChecked():
            self.reanalyze()

    def get_extensions(self):
        if self.mode.currentText() != "Custom":
            return None
        if not self.custom_filter_enabled.isChecked():
            return None
        raw = self.extensions.text().strip()
        if not raw:
            return None
        result = set()
        for part in raw.replace(",", ";").split(";"):
            part = part.strip()
            if not part:
                continue
            if not part.startswith("."):
                part = "." + part
            result.add(part.lower())
        return result

    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "ファイルを追加")
        if files:
            self.add_paths([Path(x) for x in files])

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "フォルダを追加")
        if folder:
            self.add_paths([Path(folder)])

    def clear_all(self):
        self.current_roots = []
        self.model.set_rows([])
        self.before_preview.clear()
        self.after_preview.clear()
        self.candidate_label.setText("候補: -")
        self.log.appendPlainText("一覧をクリアしました。")

    def add_paths(self, paths):
        for p in paths:
            if p not in self.current_roots:
                self.current_roots.append(p)
        self.reanalyze()

    def reanalyze(self):
        if not self.current_roots:
            return

        files = collect_all_files(
            self.current_roots,
            recursive=self.recursive.isChecked(),
            extensions=self.get_extensions()
        )

        target = output_encoding_for_mode(
            self.mode.currentText(), self.output_encoding.currentText()
        )

        rows = []
        text_count = binary_count = unknown_count = 0

        for p in files:
            try:
                data = p.read_bytes()
                result = detect_encoding(data)

                if result.binary:
                    binary_count += 1
                    rows.append({
                        "enabled": False,
                        "checkable": False,
                        "path": p,
                        "detected": "BINARY",
                        "confidence": None,
                        "target": "そのまま",
                        "status": "変換対象外（チェック時は元のままコピー）",
                        "detection": result,
                    })
                elif result.encoding is None:
                    unknown_count += 1
                    rows.append({
                        "enabled": False,
                        "checkable": True,
                        "path": p,
                        "detected": "UNKNOWN",
                        "confidence": result.confidence,
                        "target": target,
                        "status": "自動判定不能（チェック時は元のままコピー）",
                        "detection": result,
                    })
                else:
                    text_count += 1
                    rows.append({
                        "enabled": True,
                        "checkable": True,
                        "path": p,
                        "detected": result.encoding,
                        "confidence": result.confidence,
                        "target": target,
                        "status": result.reason,
                        "detection": result,
                    })
            except Exception as e:
                rows.append({
                    "enabled": False,
                    "checkable": False,
                    "path": p,
                    "detected": "ERROR",
                    "confidence": None,
                    "target": "",
                    "status": str(e),
                    "detection": None,
                })

        self.model.set_rows(rows)
        self.log.appendPlainText(
            f"走査: {len(rows)} / テキスト: {text_count} / バイナリ: {binary_count} / 不明: {unknown_count}"
        )
        if rows and self.mode.currentText() == "Custom":
            self.preview_index(0)

    def preview_row(self, index):
        if self.mode.currentText() == "Custom":
            self.preview_index(index.row())

    def refresh_preview(self):
        if self.mode.currentText() != "Custom":
            return
        idx = self.table.currentIndex()
        if idx.isValid():
            self.preview_index(idx.row())

    def preview_index(self, row_index):
        if row_index < 0 or row_index >= len(self.model.rows):
            return
        row = self.model.rows[row_index]
        path = row["path"]

        try:
            data = path.read_bytes()
        except Exception as e:
            self.before_preview.setPlainText(str(e))
            self.after_preview.clear()
            return

        detection = row.get("detection")
        if detection and detection.candidates:
            parts = []
            for name, ok, score in detection.candidates:
                parts.append(f"{name}: {'OK' if ok else 'NG'}" + (f" ({score:.1f})" if ok else ""))
            self.candidate_label.setText("候補: " + " / ".join(parts))
        else:
            self.candidate_label.setText("候補: -")

        in_enc = self.input_encoding.currentText()
        if in_enc == "Auto":
            in_enc = row["detected"]

        if in_enc in ("BINARY", "UNKNOWN", "ERROR"):
            self.before_preview.setPlainText("入力文字コードを手動で選択してください。")
            self.after_preview.clear()
            return

        try:
            text = decode_with_encoding(data, in_enc)
            self.before_preview.setPlainText(self._preview_text(text))
        except Exception as e:
            self.before_preview.setPlainText(f"デコード失敗: {e}")
            self.after_preview.clear()
            return

        out_enc = self.output_encoding.currentText()
        try:
            encoded = text.encode(self._codec(out_enc), errors="strict")
            check = encoded.decode(self._codec(out_enc), errors="strict")
            self.after_preview.setPlainText(self._preview_text(check))
        except Exception as e:
            self.after_preview.setPlainText(f"変換不可: {e}")

    @staticmethod
    def _codec(name):
        return {
            "UTF-8": "utf-8",
            "UTF-8 BOM": "utf-8-sig",
            "CP932": "cp932",
            "Shift_JIS": "shift_jis",
            "EUC-JP": "euc_jp",
            "ISO-2022-JP": "iso2022_jp",
        }.get(name, name)

    @staticmethod
    def _preview_text(text, limit=30000):
        if len(text) > limit:
            return text[:limit] + "\n\n--- プレビューは先頭のみ ---"
        return text

    def convert_selected(self):
        if not self.model.rows:
            QMessageBox.information(self, "変換", "ファイルがありません。")
            return

        selected_text = [r for r in self.model.rows if r["enabled"]]
        if not selected_text:
            QMessageBox.information(self, "変換", "変換対象のテキストファイルがありません。")
            return

        # 単一ファイルだけを直接投入した場合は「名前を付けて保存」
        single_direct_file = (
            len(self.current_roots) == 1 and
            self.current_roots[0].is_file() and
            len(self.model.rows) == 1 and
            not self.overwrite.isChecked()
        )

        output_root = None
        single_output = None

        if self.overwrite.isChecked():
            ret = QMessageBox.warning(
                self, "上書き確認",
                "元ファイルを上書きします。続行しますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if ret != QMessageBox.Yes:
                return
        elif single_direct_file:
            src = self.current_roots[0]

            # 単一ファイルは _encoded を付けない。
            # 保存ダイアログの初期名も元ファイル名のまま。
            path, _ = QFileDialog.getSaveFileName(
                self, "変換後ファイルを保存", str(src)
            )
            if not path:
                return
            single_output = Path(path)
        else:
            # 1フォルダを投入した場合は、同じ階層に
            # <元フォルダ名>_encoded を自動作成する。
            folder_roots = [p for p in self.current_roots if p.is_dir()]

            if len(folder_roots) == 1 and len(self.current_roots) == 1:
                root = folder_roots[0]
                output_root = root.parent / (root.name + "_encoded")
            else:
                # 複数のルートを一度に追加した場合の共通出力先
                base_parent = self.current_roots[0].parent
                output_root = base_parent / "encoding_output_encoded"

            if output_root.exists():
                ret = QMessageBox.question(
                    self,
                    "出力フォルダ確認",
                    "出力先が既に存在します。\n\n{}\n\n"
                    "既存ファイルを必要に応じて上書きして続行しますか？".format(output_root),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if ret != QMessageBox.Yes:
                    return

            self.log.appendPlainText("出力先: {}".format(output_root))

        ok = ng = copied = 0

        for row in self.model.rows:
            src = row["path"]

            if self.overwrite.isChecked():
                dst = src
            elif single_output is not None:
                dst = single_output
            else:
                dst = self.destination_for(src, output_root)

            if row["detected"] == "BINARY":
                if not self.overwrite.isChecked() and self.copy_nontext.isChecked():
                    try:
                        copy_file_to_path(src, dst)
                        copied += 1
                        row["status"] = "コピー済み（非変換）"
                    except Exception as e:
                        ng += 1
                        row["status"] = "コピー失敗: " + str(e)
                continue

            if not row["enabled"]:
                if not self.overwrite.isChecked() and self.copy_nontext.isChecked():
                    try:
                        copy_file_to_path(src, dst)
                        copied += 1
                        row["status"] = "コピー済み（未判定）"
                    except Exception as e:
                        ng += 1
                        row["status"] = "コピー失敗: " + str(e)
                continue

            in_enc = self.input_encoding.currentText()
            if self.mode.currentText() != "Custom" or in_enc == "Auto":
                in_enc = row["detected"]

            out_enc = output_encoding_for_mode(
                self.mode.currentText(), self.output_encoding.currentText()
            )

            try:
                convert_file_to_path(
                    src, dst,
                    input_encoding=in_enc,
                    output_encoding=out_enc,
                    preserve_eol=self.preserve_eol.isChecked(),
                    backup=self.overwrite.isChecked() and self.make_backup.isChecked()
                )
                ok += 1
                row["status"] = "変換済み"
                self.log.appendPlainText(f"[OK] {src} : {in_enc} -> {out_enc}")
            except Exception as e:
                # 変換に失敗しても、別フォルダ出力かつチェックONなら
                # 元ファイルをそのままコピーして、プロジェクト構成を維持する。
                if (
                    not self.overwrite.isChecked()
                    and self.copy_nontext.isChecked()
                ):
                    try:
                        copy_file_to_path(src, dst)
                        copied += 1
                        row["status"] = "変換失敗 → 元のままコピー"
                        self.log.appendPlainText(
                            f"[COPY] {src} : 変換失敗のため元ファイルをコピー ({e})"
                        )
                    except Exception as copy_error:
                        ng += 1
                        row["status"] = "変換/コピー失敗: " + str(copy_error)
                        self.log.appendPlainText(
                            f"[NG] {src} : 変換={e} / コピー={copy_error}"
                        )
                else:
                    ng += 1
                    row["status"] = "失敗: " + str(e)
                    self.log.appendPlainText(f"[NG] {src} : {e}")

        self.model.layoutChanged.emit()
        QMessageBox.information(
            self, "完了",
            f"変換成功: {ok}\n元のままコピー: {copied}\n保存できなかったファイル: {ng}"
        )

    def destination_for(self, src, output_root):
        folder_roots = [p for p in self.current_roots if p.is_dir()]

        # 1フォルダだけの場合:
        #   project/sub/foo.m
        # -> project_encoded/sub/foo.m
        if len(folder_roots) == 1 and len(self.current_roots) == 1:
            root = folder_roots[0]
            try:
                rel = src.relative_to(root)
                return output_root / rel
            except ValueError:
                return output_root / src.name

        # 複数ルートの場合はファイル名衝突防止のためルート名も残す
        for root in folder_roots:
            try:
                rel = src.relative_to(root)
                return output_root / root.name / rel
            except ValueError:
                pass

        return output_root / src.name


def main():
    app = QApplication(sys.argv)

    # GUI全体を見やすい大きさにする
    font = app.font()
    font.setPointSize(12)
    app.setFont(font)

    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
