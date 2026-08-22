#!/usr/bin/env python3
"""
Magnet2Torrent
==============
A small PyQt5 desktop app for KDE/Debian that:
  1. Stores one or more SSH server profiles (host, port, user, target directory,
     and either passwordless SSH-key auth or a password).
  2. Takes a magnet link (typed, pasted, or loaded from a .magnet file),
     fetches its metadata via libtorrent/DHT, and builds a real .torrent file.
  3. Uploads the resulting .torrent file to the target directory on the
     chosen server via scp, using either your SSH key or a stored/typed password.

Dependencies (Debian):
    sudo apt install python3-pyqt5 python3-libtorrent openssh-client sshpass

(sshpass is only needed if you use password auth for a server profile.)

Run:
    python3 magnet2torrent.py
"""

import sys
import os
import json
import subprocess
import tempfile
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QListWidget, QListWidgetItem, QTabWidget,
    QTextEdit, QFileDialog, QMessageBox, QComboBox, QSpinBox, QGroupBox,
    QDialog, QDialogButtonBox, QProgressBar, QCheckBox, QInputDialog
)
from PyQt5.QtCore import QThread, pyqtSignal

CONFIG_DIR = Path.home() / ".config" / "magnet2torrent"
CONFIG_FILE = CONFIG_DIR / "servers.json"


def load_servers():
    if not CONFIG_FILE.exists():
        return []
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_servers(servers):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(servers, f, indent=2)
    try:
        # Config may contain saved passwords -- keep it readable only by the owner.
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Background workers (so the GUI never freezes)
# --------------------------------------------------------------------------

class MetadataWorker(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(str)   # path to the generated .torrent file
    finished_err = pyqtSignal(str)

    def __init__(self, magnet_uri, save_dir, timeout=120):
        super().__init__()
        self.magnet_uri = magnet_uri
        self.save_dir = save_dir
        self.timeout = timeout
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import libtorrent as lt
        except ImportError:
            self.finished_err.emit(
                "python3-libtorrent is not installed.\n"
                "Install it with: sudo apt install python3-libtorrent"
            )
            return

        import time

        try:
            ses = lt.session()
            ses.listen_on(6881, 6891)
            ses.add_dht_router("router.bittorrent.com", 6881)
            ses.add_dht_router("router.utorrent.com", 6881)
            ses.add_dht_router("dht.transmissionbt.com", 6881)
            ses.start_dht()

            params = {
                "save_path": self.save_dir,
                "storage_mode": lt.storage_mode_t(2),
            }
            self.progress.emit("Parsing magnet link...")
            handle = lt.add_magnet_uri(ses, self.magnet_uri, params)

            self.progress.emit("Fetching metadata from peers/DHT (this can take a minute)...")
            start = time.time()
            while not handle.has_metadata():
                if self._cancelled:
                    self.finished_err.emit("Cancelled.")
                    return
                if time.time() - start > self.timeout:
                    self.finished_err.emit(
                        "Timed out waiting for metadata. Try again, or check the "
                        "magnet link / your internet connection."
                    )
                    return
                time.sleep(1)

            self.progress.emit("Metadata received, building .torrent file...")
            ti = handle.get_torrent_info()
            torrent_file = lt.create_torrent(ti)
            torrent_path = os.path.join(self.save_dir, ti.name() + ".torrent")
            with open(torrent_path, "wb") as f:
                f.write(lt.bencode(torrent_file.generate()))

            ses.remove_torrent(handle)
            self.finished_ok.emit(torrent_path)
        except Exception as e:
            self.finished_err.emit(str(e))


class TransferWorker(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal()
    finished_err = pyqtSignal(str)

    def __init__(self, local_path, server, password=None):
        super().__init__()
        self.local_path = local_path
        self.server = server
        self.password = password  # only used when server['auth'] == 'password'

    def run(self):
        server = self.server
        remote = f"{server['user']}@{server['host']}:{server['target_dir'].rstrip('/')}/"
        scp_cmd = [
            "scp",
            "-P", str(server.get("port", 22)),
            "-o", "StrictHostKeyChecking=accept-new",
            self.local_path,
            remote,
        ]

        if server.get("auth") == "password":
            cmd = ["sshpass", "-p", self.password] + scp_cmd
            self.progress.emit("Running: sshpass -p **** " + " ".join(scp_cmd))
        else:
            cmd = ["scp", "-o", "BatchMode=yes"] + scp_cmd[1:]
            self.progress.emit("Running: " + " ".join(cmd))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                self.finished_ok.emit()
            else:
                self.finished_err.emit(result.stderr.strip() or "scp failed with no error output.")
        except subprocess.TimeoutExpired:
            self.finished_err.emit("scp timed out.")
        except FileNotFoundError as e:
            if "sshpass" in str(e):
                self.finished_err.emit("sshpass not found. Install it with: sudo apt install sshpass")
            else:
                self.finished_err.emit("scp not found. Install it with: sudo apt install openssh-client")
        except Exception as e:
            self.finished_err.emit(str(e))


# --------------------------------------------------------------------------
# Server profile dialog
# --------------------------------------------------------------------------

class ServerDialog(QDialog):
    def __init__(self, parent=None, server=None):
        super().__init__(parent)
        self.setWindowTitle("Server Profile")
        server = server or {}

        self.name_edit = QLineEdit(server.get("name", ""))
        self.host_edit = QLineEdit(server.get("host", ""))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(server.get("port", 22))
        self.user_edit = QLineEdit(server.get("user", ""))
        self.dir_edit = QLineEdit(server.get("target_dir", ""))

        self.auth_combo = QComboBox()
        self.auth_combo.addItem("SSH key (passwordless)", "key")
        self.auth_combo.addItem("Password", "password")
        auth_index = 1 if server.get("auth") == "password" else 0
        self.auth_combo.setCurrentIndex(auth_index)
        self.auth_combo.currentIndexChanged.connect(self.update_password_visibility)

        self.password_edit = QLineEdit(server.get("password", "") or "")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.remember_check = QCheckBox("Remember password (stored in plain text on this machine)")
        self.remember_check.setChecked(bool(server.get("remember_password", False)))

        form = QFormLayout()
        form.addRow("Profile name:", self.name_edit)
        form.addRow("Host / IP:", self.host_edit)
        form.addRow("SSH port:", self.port_spin)
        form.addRow("Username:", self.user_edit)
        form.addRow("Target directory:", self.dir_edit)
        form.addRow("Authentication:", self.auth_combo)
        form.addRow("Password:", self.password_edit)
        form.addRow("", self.remember_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.update_password_visibility()

    def update_password_visibility(self):
        is_password = self.auth_combo.currentData() == "password"
        self.password_edit.setVisible(is_password)
        self.remember_check.setVisible(is_password)

    def get_data(self):
        auth = self.auth_combo.currentData()
        remember = self.remember_check.isChecked() if auth == "password" else False
        password = self.password_edit.text() if (auth == "password" and remember) else ""
        return {
            "name": self.name_edit.text().strip(),
            "host": self.host_edit.text().strip(),
            "port": self.port_spin.value(),
            "user": self.user_edit.text().strip(),
            "target_dir": self.dir_edit.text().strip(),
            "auth": auth,
            "remember_password": remember,
            "password": password,
        }


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Magnet2Torrent")
        self.resize(680, 520)

        self.servers = load_servers()
        self.metadata_worker = None
        self.transfer_worker = None
        self.torrent_path = None
        self.pending_password = None

        tabs = QTabWidget()
        tabs.addTab(self.build_convert_tab(), "Convert && Send")
        tabs.addTab(self.build_servers_tab(), "Servers")
        self.setCentralWidget(tabs)

        self.refresh_server_list()
        self.refresh_server_combo()

    # ---------------- Convert & Send tab ----------------

    def build_convert_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        magnet_group = QGroupBox("Magnet link")
        magnet_layout = QVBoxLayout(magnet_group)
        self.magnet_edit = QLineEdit()
        self.magnet_edit.setPlaceholderText("magnet:?xt=urn:btih:...")
        load_btn = QPushButton("Load from .magnet file...")
        load_btn.clicked.connect(self.load_magnet_file)
        magnet_row = QHBoxLayout()
        magnet_row.addWidget(self.magnet_edit)
        magnet_row.addWidget(load_btn)
        magnet_layout.addLayout(magnet_row)
        layout.addWidget(magnet_group)

        target_group = QGroupBox("Target server")
        target_layout = QHBoxLayout(target_group)
        self.server_combo = QComboBox()
        test_btn = QPushButton("Test connection")
        test_btn.clicked.connect(self.test_connection)
        target_layout.addWidget(self.server_combo)
        target_layout.addWidget(test_btn)
        layout.addWidget(target_group)

        action_row = QHBoxLayout()
        self.convert_btn = QPushButton("Convert && Send")
        self.convert_btn.clicked.connect(self.start_conversion)
        action_row.addWidget(self.convert_btn)
        layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        return w

    def load_magnet_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select magnet file", "", "Magnet/Text files (*.magnet *.txt);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r") as f:
                content = f.read().strip()
            self.magnet_edit.setText(content)
        except OSError as e:
            QMessageBox.warning(self, "Error", f"Could not read file:\n{e}")

    def append_log(self, text):
        self.log.append(text)

    def current_server(self):
        idx = self.server_combo.currentIndex()
        if idx < 0 or idx >= len(self.servers):
            return None
        return self.servers[idx]

    def get_password(self, server):
        """Return the password to use for this server, or None if the user cancelled."""
        if server.get("auth") != "password":
            return None
        if server.get("remember_password") and server.get("password"):
            return server["password"]
        pwd, ok = QInputDialog.getText(
            self, "Password required",
            f"Password for {server['user']}@{server['host']}:",
            QLineEdit.Password
        )
        if not ok:
            return None
        return pwd

    def test_connection(self):
        server = self.current_server()
        if not server:
            QMessageBox.warning(self, "No server", "Please add and select a server profile first.")
            return

        ssh_cmd = [
            "ssh", "-p", str(server.get("port", 22)),
            "-o", "ConnectTimeout=8",
            f"{server['user']}@{server['host']}",
            "echo OK",
        ]

        if server.get("auth") == "password":
            password = self.get_password(server)
            if password is None:
                return
            cmd = ["sshpass", "-p", password] + ssh_cmd
            self.append_log("Testing connection: sshpass -p **** " + " ".join(ssh_cmd))
        else:
            cmd = ["ssh", "-o", "BatchMode=yes"] + ssh_cmd[1:]
            self.append_log("Testing connection: " + " ".join(cmd))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and "OK" in result.stdout:
                self.append_log("Connection OK.")
                QMessageBox.information(self, "Success", "SSH connection succeeded.")
            else:
                err = result.stderr.strip() or "Unknown error"
                self.append_log("Connection failed: " + err)
                QMessageBox.warning(self, "Failed", f"SSH connection failed:\n{err}")
        except FileNotFoundError as e:
            msg = ("sshpass not found. Install it with: sudo apt install sshpass"
                   if "sshpass" in str(e) else str(e))
            self.append_log("Connection failed: " + msg)
            QMessageBox.warning(self, "Failed", msg)
        except Exception as e:
            self.append_log("Connection failed: " + str(e))
            QMessageBox.warning(self, "Failed", str(e))

    def start_conversion(self):
        magnet = self.magnet_edit.text().strip()
        if not magnet.startswith("magnet:"):
            QMessageBox.warning(self, "Invalid magnet", "Please enter or load a valid magnet: link.")
            return
        server = self.current_server()
        if not server:
            QMessageBox.warning(self, "No server", "Please add and select a server profile first.")
            return

        # Grab the password up front (if needed) so we don't interrupt the user
        # again once the background metadata fetch finishes.
        self.pending_password = None
        if server.get("auth") == "password":
            self.pending_password = self.get_password(server)
            if self.pending_password is None:
                return

        self.convert_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.append_log("---")
        self.append_log("Starting conversion...")

        tmp_dir = tempfile.mkdtemp(prefix="magnet2torrent_")
        self.metadata_worker = MetadataWorker(magnet, tmp_dir)
        self.metadata_worker.progress.connect(self.append_log)
        self.metadata_worker.finished_ok.connect(self.on_metadata_ok)
        self.metadata_worker.finished_err.connect(self.on_metadata_err)
        self.metadata_worker.start()

    def on_metadata_err(self, msg):
        self.append_log("Error: " + msg)
        self.progress_bar.setVisible(False)
        self.convert_btn.setEnabled(True)
        QMessageBox.warning(self, "Conversion failed", msg)

    def on_metadata_ok(self, torrent_path):
        self.torrent_path = torrent_path
        self.append_log(f"Torrent file created: {torrent_path}")
        self.append_log("Uploading to server...")

        server = self.current_server()
        self.transfer_worker = TransferWorker(torrent_path, server, password=self.pending_password)
        self.transfer_worker.progress.connect(self.append_log)
        self.transfer_worker.finished_ok.connect(self.on_transfer_ok)
        self.transfer_worker.finished_err.connect(self.on_transfer_err)
        self.transfer_worker.start()

    def on_transfer_ok(self):
        self.append_log("Upload complete.")
        self.progress_bar.setVisible(False)
        self.convert_btn.setEnabled(True)
        QMessageBox.information(self, "Done", "Torrent file created and sent to the server.")

    def on_transfer_err(self, msg):
        self.append_log("Upload failed: " + msg)
        self.progress_bar.setVisible(False)
        self.convert_btn.setEnabled(True)
        QMessageBox.warning(self, "Upload failed", msg)

    # ---------------- Servers tab ----------------

    def build_servers_tab(self):
        w = QWidget()
        layout = QHBoxLayout(w)

        self.server_list = QListWidget()
        layout.addWidget(self.server_list, 1)

        btn_col = QVBoxLayout()
        add_btn = QPushButton("Add...")
        add_btn.clicked.connect(self.add_server)
        edit_btn = QPushButton("Edit...")
        edit_btn.clicked.connect(self.edit_server)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self.delete_server)
        btn_col.addWidget(add_btn)
        btn_col.addWidget(edit_btn)
        btn_col.addWidget(del_btn)
        btn_col.addStretch()
        layout.addLayout(btn_col)

        return w

    def refresh_server_list(self):
        self.server_list.clear()
        for s in self.servers:
            auth = "password" if s.get("auth") == "password" else "SSH key"
            self.server_list.addItem(QListWidgetItem(
                f"{s['name']}  ({s['user']}@{s['host']}:{s.get('port', 22)} -> {s['target_dir']})  [{auth}]"
            ))

    def refresh_server_combo(self):
        self.server_combo.clear()
        for s in self.servers:
            self.server_combo.addItem(s['name'])

    def add_server(self):
        dlg = ServerDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            if not data["name"] or not data["host"] or not data["user"] or not data["target_dir"]:
                QMessageBox.warning(self, "Missing data", "All fields except port are required.")
                return
            self.servers.append(data)
            save_servers(self.servers)
            self.refresh_server_list()
            self.refresh_server_combo()

    def edit_server(self):
        row = self.server_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "No selection", "Select a server profile to edit.")
            return
        dlg = ServerDialog(self, self.servers[row])
        if dlg.exec_() == QDialog.Accepted:
            self.servers[row] = dlg.get_data()
            save_servers(self.servers)
            self.refresh_server_list()
            self.refresh_server_combo()

    def delete_server(self):
        row = self.server_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "No selection", "Select a server profile to delete.")
            return
        confirm = QMessageBox.question(self, "Confirm", "Delete this server profile?")
        if confirm == QMessageBox.Yes:
            del self.servers[row]
            save_servers(self.servers)
            self.refresh_server_list()
            self.refresh_server_combo()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
