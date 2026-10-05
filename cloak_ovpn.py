"""DGCloak VPN — Cloak (ck-client) + OpenVPN (консольный openvpn.exe), лаунчер для Windows.

Без окон стороннего GUI, статус берётся из management-интерфейса OpenVPN.
Требует прав администратора (создание сетевого адаптера и маршрутов).
Для иконки и трея нужны pystray и pillow (без них программа работает, но без трея).
"""
import base64
import ctypes
import io
import json
import os
import queue
import re
import socket
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import pystray
    import cloak_icon
    from PIL import Image  # noqa: F401  (нужен pystray)
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

APP_NAME = "DGCloak VPN"
BTN_W = 16   # одинаковая ширина кнопок «Подключить/Отключить» и «Дополнительно»
APP_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloakVPN")
DATA_FILE = os.path.join(APP_DIR, "data.json")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULT_DATA = {
    "ck_client": r"C:\Tools\Cloak\ck-client-windows-amd64.exe",
    "openvpn_exe": r"C:\Program Files\OpenVPN\bin\openvpn.exe",
    "profiles": [],
    "last_profile": "",
}

# состояние OpenVPN -> (текст, цвет)
STATES = {
    "RESOLVE": ("Подключение…", "orange"),
    "TCP_CONNECT": ("Подключение…", "orange"),
    "CONNECTING": ("Подключение…", "orange"),
    "WAIT": ("Ожидание ответа сервера…", "orange"),
    "AUTH": ("Аутентификация…", "orange"),
    "GET_CONFIG": ("Получение настроек…", "orange"),
    "ASSIGN_IP": ("Назначение IP…", "orange"),
    "ADD_ROUTES": ("Добавление маршрутов…", "orange"),
    "CONNECTED": ("Подключено: {name}", "green"),
    "RECONNECTING": ("Переподключение…", "orange"),
    "EXITING": ("Отключение…", "orange"),
}


# строка состояния: ">STATE:ts,CONNECTED,..." (реальное время) или "ts,CONNECTED,..." (история)
STATE_RE = re.compile(r"^(?:>STATE:)?\d+,([A-Z_]+),")


def load_data():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return {**DEFAULT_DATA, **json.load(f)}
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT_DATA))


def save_data(data):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def probe(host, port, timeout=2.0):
    """None, если порт принимает соединения, иначе текст ошибки."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as e:
        return repr(e)


def port_open(host, port):
    return probe(host, port) is None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def loopback_ok():
    """Работает ли вообще локальный TCP на 127.0.0.1."""
    try:
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            with socket.create_connection(srv.getsockname(), timeout=2):
                return True
    except OSError:
        return False


def repair_loopback():
    """Вернуть системный маршрут loopback (OpenVPN может его удалить при выходе).
    True — маршрута не было и он добавлен; False — уже существовал (штатно) или не вышло."""
    try:
        r = subprocess.run(["route", "ADD", "127.0.0.1", "MASK", "255.255.255.255", "0.0.0.0", "IF", "1"],
                           capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
                           creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def find_procs(exe_name):
    """PID уже запущенных процессов с таким именем."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == exe_name.lower():
            pids.append(parts[1])
    return pids


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


class ProfileDialog(tk.Toplevel):
    FIELDS = [
        ("name", "Название", None),
        ("ck_config", "Cloak конфиг (.json)", [("JSON", "*.json"), ("Все", "*.*")]),
        ("ovpn", "OpenVPN профиль (.ovpn)", [("OVPN", "*.ovpn"), ("Все", "*.*")]),
        ("port", "Локальный порт Cloak (-l)", None),
        ("server", "Сервер Cloak (-s), если не в конфиге", None),
        ("server_port", "Порт сервера Cloak (-p)", None),
        ("bypass_ip", "IP сервера Cloak: исключить из туннеля", None),
    ]

    def __init__(self, parent, profile=None):
        super().__init__(parent)
        self.title("Профиль")
        self.resizable(False, False)
        self.result = None
        p = profile or {}
        self.vars = {}
        for i, (key, label, ftypes) in enumerate(self.FIELDS):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="w", padx=8, pady=4)
            v = tk.StringVar(value=str(p.get(key, "1984" if key == "port" else "")))
            self.vars[key] = v
            ttk.Entry(self, textvariable=v, width=48).grid(row=i, column=1, padx=4)
            if ftypes:
                ttk.Button(self, text="…", width=3,
                           command=lambda v=v, t=ftypes: self._browse(v, t)).grid(row=i, column=2, padx=4)
        self.udp = tk.BooleanVar(value=p.get("udp", False))
        self.full = tk.BooleanVar(value=p.get("full_tunnel", True))
        n = len(self.FIELDS)
        ttk.Checkbutton(self, text="UDP-режим (-u, для OpenVPN по UDP)",
                        variable=self.udp).grid(row=n, column=1, sticky="w", pady=4)
        ttk.Checkbutton(self, text="Весь трафик через VPN (redirect-gateway local def1)",
                        variable=self.full).grid(row=n + 1, column=1, sticky="w")
        ttk.Label(self, foreground="gray",
                  text="В .ovpn: remote 127.0.0.1 <порт Cloak>. IP-обход добавит маршрут через\n"
                       "основной шлюз, чтобы трафик Cloak не заворачивался в сам VPN."
                  ).grid(row=n + 2, column=0, columnspan=3, padx=8, sticky="w")
        btns = ttk.Frame(self)
        btns.grid(row=n + 3, column=0, columnspan=3, pady=8)
        ttk.Button(btns, text="Сохранить", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(side="left", padx=4)
        self.transient(parent)
        self.grab_set()

    def _browse(self, var, ftypes):
        path = filedialog.askopenfilename(filetypes=ftypes)
        if path:
            var.set(os.path.normpath(path))
            if var is self.vars["ck_config"] and not self.vars["name"].get():
                self.vars["name"].set(os.path.splitext(os.path.basename(path))[0])

    def _ok(self):
        r = {k: v.get().strip() for k, v in self.vars.items()}
        if not (r["name"] and r["ck_config"] and r["ovpn"]):
            messagebox.showerror("Ошибка", "Заполните название, конфиг Cloak и профиль OpenVPN.", parent=self)
            return
        try:
            r["port"] = int(r["port"] or 1984)
        except ValueError:
            messagebox.showerror("Ошибка", "Порт должен быть числом.", parent=self)
            return
        r["udp"] = self.udp.get()
        r["full_tunnel"] = self.full.get()
        self.result = r
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.minsize(470, 10)
        self.data = load_data()
        self.ck = None
        self.vpn = None
        self.mgmt = None
        self.active = None
        self.busy = False
        self.up = threading.Event()
        self._mon_stop = None
        self.uiq = queue.Queue()
        self.last_state = None
        self.route_failed = False
        self.verb = False
        self.tray = None
        self.cur_name = ""
        self._icons = {}
        self._last_tray_text = None
        self.verbose = tk.BooleanVar(value=False)
        self.adv_open = False
        self._hint_shown = False
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            self.logfile = open(os.path.join(APP_DIR, "last.log"), "w", encoding="utf-8", buffering=1)
        except OSError:
            self.logfile = None
        threading.excepthook = lambda a: self._report_exc(a.exc_type, a.exc_value, a.exc_traceback)
        self._build()
        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), 10)
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_x)
        self.bind("<Unmap>", self._on_unmap)
        self._init_icons()
        if not is_admin():
            self.say("Внимание: нет прав администратора. OpenVPN не сможет создать адаптер — "
                     "запустите программу от имени администратора.")

    # ---------- UI ----------
    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Label(top, text="Профиль:").pack(side="left")
        self.combo = ttk.Combobox(top, state="readonly", width=30)
        self.combo.pack(side="left", padx=6)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._sync_current())
        self.b_add = ttk.Button(top, text="+", width=3, command=self._add)
        self.b_edit = ttk.Button(top, text="Изм.", width=5, command=self._edit)
        self.b_del = ttk.Button(top, text="Удал.", width=5, command=self._delete)
        for b in (self.b_add, self.b_edit, self.b_del):
            b.pack(side="left", padx=2)
        ttk.Button(top, text="Выход", width=7, command=self._exit_clicked).pack(side="right")

        mid = ttk.Frame(self)
        mid.pack(fill="x", padx=10, pady=(0, 6))
        self.btn = ttk.Button(mid, text="Подключить", width=BTN_W, command=self._toggle)
        self.btn.pack(side="left")
        self.status = ttk.Label(mid, text="Отключено", foreground="gray", wraplength=380)
        self.status.pack(side="left", padx=12)

        advrow = ttk.Frame(self)
        advrow.pack(fill="x", padx=10, pady=(0, 10))
        self.b_adv = ttk.Button(advrow, text="Дополнительно ▾", width=BTN_W, command=self._toggle_adv)
        self.b_adv.pack(side="left")

        # Скрываемая панель: лог и редко нужные настройки
        self.adv = ttk.Frame(self)
        bar = ttk.Frame(self.adv)
        bar.pack(fill="x")
        ttk.Button(bar, text="Копировать лог", command=self._copy_log).pack(side="left")
        self.b_paths = ttk.Button(bar, text="Пути к программам…", command=self._paths)
        self.b_paths.pack(side="left", padx=6)
        ttk.Checkbutton(self.adv, text="Подробный лог OpenVPN (применится при следующем подключении)",
                        variable=self.verbose).pack(anchor="w", pady=(6, 0))
        self.log = tk.Text(self.adv, height=16, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(6, 0))
        self._refresh_combo(self.data.get("last_profile"))

    def _exit_clicked(self):
        if self.active and not messagebox.askyesno(APP_NAME, "VPN подключён. Отключить и выйти из программы?"):
            return
        self._quit()

    def _toggle_adv(self):
        if self.adv_open:
            self._hide_adv()
        else:
            self._show_adv()

    def _hide_adv(self):
        self.adv.pack_forget()
        self.b_adv.config(text="Дополнительно ▾")
        self.geometry("")                # вернуть компактный размер
        self.adv_open = False

    def _show_adv(self):
        if not self.adv_open:            # флаг ведём сами: winfo_ismapped() отстаёт от pack()
            self.adv.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            self.b_adv.config(text="Дополнительно ▴")
            self.geometry("720x560")
            self.adv_open = True

    def _refresh_combo(self, select=None):
        names = [p["name"] for p in self.data["profiles"]]
        self.combo["values"] = names
        if select in names:
            self.combo.set(select)
        elif names and self.combo.get() not in names:
            self.combo.current(0)
        elif not names:
            self.combo.set("")
        self._sync_current()

    def _sync_current(self):
        # cur_name читается из потока трея, поэтому дублируем выбор в обычной переменной
        self.cur_name = self.combo.get()
        self._refresh_tray_menu()

    def _refresh_tray_menu(self):
        if self.tray:
            try:
                self.tray.update_menu()
            except Exception as e:  # noqa: BLE001
                self._log_file(f"[tray menu] {e!r}\n")

    def _current(self):
        name = self.combo.get()
        return next((p for p in self.data["profiles"] if p["name"] == name), None)

    # Любое обновление интерфейса из потоков идёт через очередь
    def ui(self, fn):
        self.uiq.put(fn)

    def _log_file(self, line):
        if self.logfile:
            try:
                self.logfile.write(line)
            except (OSError, ValueError):
                pass

    def say(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"
        self._log_file(line)
        self.ui(lambda: self._append(line))

    def _report_exc(self, exc, val, tb):
        import traceback
        self.say("ВНУТРЕННЯЯ ОШИБКА:\n" + "".join(traceback.format_exception(exc, val, tb)))

    def report_callback_exception(self, exc, val, tb):  # исключения внутри Tk-колбэков
        self._report_exc(exc, val, tb)

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log.get("1.0", "end"))
        self.say("Лог скопирован в буфер обмена.")

    def _append(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.config(state="disabled")

    def set_status(self, text, color):
        def apply():
            self.status.config(text=text, foreground=color)
            self._tray_update(text, color)
        self.ui(apply)

    # ---------- иконка и трей ----------
    def _init_icons(self):
        if not HAS_TRAY:
            self.say("Иконка и трей отключены: установите пакеты  pip install pystray pillow")
            return
        try:
            self._icons = {k: cloak_icon.draw_tray_icon(v, 64) for k, v in cloak_icon.STATUS_COLORS.items()}
            buf = io.BytesIO()
            cloak_icon.draw_icon(cloak_icon.BRAND, 64).save(buf, "PNG")
            self._win_icon = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
            self.iconphoto(True, self._win_icon)
            menu = pystray.Menu(self._tray_items)   # меню пересобирается при update_menu()
            self.tray = pystray.Icon("DGCloakVPN", self._icons["gray"], f"{APP_NAME}: отключено", menu)
            self.tray.run_detached()
        except Exception as e:  # noqa: BLE001
            self.tray = None
            self.say(f"Не удалось создать иконку в трее: {e!r}")

    def _tray_items(self):
        MI = pystray.MenuItem
        idle = lambda it: not self.busy  # noqa: E731
        yield MI("Открыть", lambda i, it: self.ui(self._show), default=True)
        if self.active:
            yield MI(f"Отключить «{self.active['name']}»", lambda i, it: self.ui(self._toggle), enabled=idle)
        else:
            names = [p["name"] for p in self.data["profiles"]]
            if self.cur_name in names:
                yield MI(f"Подключить текущий: «{self.cur_name}»",
                         lambda i, it: self.ui(self._toggle), enabled=idle)
            if names:
                yield MI("Подключить", pystray.Menu(self._tray_profile_items), enabled=idle)
        yield pystray.Menu.SEPARATOR
        yield MI("Выход", lambda i, it: self.ui(self._quit))

    def _tray_profile_items(self):
        for p in list(self.data["profiles"]):
            yield pystray.MenuItem(p["name"], self._tray_connect_action(p["name"]),
                                   checked=self._tray_checked(p["name"]), radio=True)

    def _tray_connect_action(self, name):
        return lambda i, it: self.ui(lambda: self._connect_named(name))

    def _tray_checked(self, name):
        return lambda it: name == self.cur_name

    def _connect_named(self, name):
        """Выбрать профиль (он становится текущим) и подключиться — вызывается из меню трея."""
        if self.busy or self.active:
            return
        if name not in [p["name"] for p in self.data["profiles"]]:
            return
        self.combo.set(name)
        self._sync_current()
        self._toggle()

    def _tray_update(self, text, color):
        if not self.tray:
            return
        try:
            self.tray.icon = self._icons.get(color, self._icons["gray"])
            self.tray.title = f"{APP_NAME}: {text}"[:120]
            self.tray.update_menu()
            changed = text != self._last_tray_text
            self._last_tray_text = text
            if changed and color in ("green", "red") and self.state() == "withdrawn":
                self.tray.notify(text, APP_NAME)
        except Exception as e:  # noqa: BLE001
            self._log_file(f"[tray] {e!r}\n")

    def _on_unmap(self, e):
        # сворачивание в панель задач -> прячем в трей
        if self.tray and e.widget is self and self.state() == "iconic":
            self.after(50, self._hide_to_tray)

    def _hide_to_tray(self):
        self.withdraw()
        if self.tray and not self._hint_shown:
            self._hint_shown = True
            try:
                self.tray.notify("Программа продолжает работать в трее. "
                                 "Выход: правый клик по значку → «Выход».", APP_NAME)
            except Exception:  # noqa: BLE001
                pass

    def _show(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def _on_x(self):
        # крестик не закрывает программу, а прячет её в трей (если трей доступен)
        if self.tray:
            self._hide_to_tray()
        else:
            self._quit()

    def _drain(self):
        try:
            while True:
                fn = self.uiq.get_nowait()
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    self._log_file(f"[ui error] {e!r}\n")
        except queue.Empty:
            pass
        finally:
            self.after(100, self._drain)

    def _set_locked(self, locked):
        state = "disabled" if locked else "normal"
        for w in (self.b_add, self.b_edit, self.b_del, self.b_paths):
            w.config(state=state)
        self.combo.config(state="disabled" if locked else "readonly")

    # ---------- профили ----------
    def _add(self):
        d = ProfileDialog(self)
        self.wait_window(d)
        if d.result:
            self.data["profiles"].append(d.result)
            save_data(self.data)
            self._refresh_combo(d.result["name"])

    def _edit(self):
        p = self._current()
        if not p:
            return
        d = ProfileDialog(self, p)
        self.wait_window(d)
        if d.result:
            old = p["name"]
            p.update(d.result)
            if self.data.get("last_profile") == old:
                self.data["last_profile"] = p["name"]
            save_data(self.data)
            self._refresh_combo(p["name"])

    def _delete(self):
        p = self._current()
        if p and messagebox.askyesno("Удалить", f"Удалить профиль «{p['name']}»?"):
            self.data["profiles"].remove(p)
            save_data(self.data)
            self._refresh_combo()

    def _paths(self):
        for key, title in (("ck_client", "ck-client.exe"), ("openvpn_exe", "openvpn.exe")):
            path = filedialog.askopenfilename(title=f"Путь к {title}",
                                              initialfile=self.data[key],
                                              filetypes=[("EXE", "*.exe")])
            if path:
                self.data[key] = os.path.normpath(path)
        save_data(self.data)

    # ---------- подключение ----------
    def _toggle(self):
        if self.busy:
            return
        if self.active:
            self._run(self._disconnect)
        else:
            p = self._current()
            if not p:
                messagebox.showinfo("Нет профиля", "Сначала добавьте профиль кнопкой «+».")
                return
            self.verb = self.verbose.get()
            self._run(lambda: self._connect(p))

    def _run(self, fn):
        self.busy = True
        self._refresh_tray_menu()
        self.btn.config(state="disabled")
        self._set_locked(True)

        def wrapper():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.say(f"Ошибка: {e}")
                self._stop_all()
                self._check_loopback()
                self.set_status(f"Ошибка: {e}", "red")
                self.ui(self._show_adv)   # открыть панель с логом, чтобы была видна причина
            finally:
                self.ui(self._finish)

        threading.Thread(target=wrapper, daemon=True).start()

    def _finish(self):
        self.busy = False
        self._refresh_tray_menu()
        self.btn.config(state="normal", text="Отключить" if self.active else "Подключить")
        self._set_locked(bool(self.active))

    def _pump(self, proc, tag):
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                if tag == "openvpn" and ("route add command failed" in line
                                         or "route addition failed" in line):
                    self.route_failed = True
                self.say(f"{tag}: {line}")

    def _connect(self, p):
        ck_exe, ov_exe = self.data["ck_client"], self.data["openvpn_exe"]
        for path in (ck_exe, ov_exe, p["ck_config"], p["ovpn"]):
            if not os.path.isfile(path):
                raise FileNotFoundError(path)
        self.up.clear()
        self.last_state = None
        self.route_failed = False

        # 0. Предполётные проверки: остатки прошлых запусков
        if repair_loopback():
            self.say("Системный маршрут 127.0.0.1 отсутствовал — восстановлен.")
        if not loopback_ok():
            raise RuntimeError("Локальный адрес 127.0.0.1 не отвечает — сломана таблица маршрутов "
                               "(обычно после аварийного завершения OpenVPN). Перезагрузите ПК.")
        for exe in (ck_exe, ov_exe):
            pids = find_procs(os.path.basename(exe))
            if pids:
                raise RuntimeError(f"Уже запущен {os.path.basename(exe)} (PID {', '.join(pids)}). "
                                   "Завершите процесс и повторите.")
        if not p.get("udp") and port_open("127.0.0.1", p["port"]):
            raise RuntimeError(f"Порт {p['port']} уже занят другим процессом.")

        # 1. Cloak
        self.set_status("Запуск Cloak…", "orange")
        args = [ck_exe, "-c", p["ck_config"], "-l", str(p["port"])]
        if p.get("server"):
            args += ["-s", p["server"]]
        if p.get("server_port"):
            args += ["-p", p["server_port"]]
        if p.get("udp"):
            args.append("-u")
        self.say("Запуск: " + " ".join(args))
        self.ck = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace",
                                   creationflags=NO_WINDOW)
        threading.Thread(target=self._pump, args=(self.ck, "cloak"), daemon=True).start()

        if p.get("udp"):
            time.sleep(3)  # в UDP-режиме порт проверить нельзя
        else:
            deadline = time.time() + 15
            while True:
                err = probe("127.0.0.1", p["port"], 1.0)
                if err is None:
                    break
                if self.ck.poll() is not None:
                    raise RuntimeError(f"Cloak завершился с кодом {self.ck.returncode}")
                if time.time() > deadline:
                    raise TimeoutError(f"Cloak не поднял порт за 15 с (ошибка: {err})")
                time.sleep(0.5)
        if self.ck.poll() is not None:
            raise RuntimeError(f"Cloak завершился с кодом {self.ck.returncode}")
        self.say("Cloak готов.")

        # 2. OpenVPN (консольный) — без GUI, статус через management
        self.set_status("Запуск OpenVPN…", "orange")
        mport = free_port()
        ov_args = [ov_exe, "--config", p["ovpn"],
                   "--cd", os.path.dirname(os.path.abspath(p["ovpn"])),
                   "--management", "127.0.0.1", str(mport),
                   "--disable-dco"]  # DCO плохо дружит с локальным прокси
        if p.get("bypass_ip"):
            ov_args += ["--route", p["bypass_ip"], "255.255.255.255", "net_gateway"]
        if p.get("full_tunnel", True):
            # Флаг local: не создавать маршрут до remote. У нас remote = 127.0.0.1,
            # и без этого флага OpenVPN при выходе удаляет системный маршрут loopback.
            ov_args += ["--redirect-gateway", "local", "def1"]
        if self.verb:
            ov_args += ["--verb", "4"]
        self.say("Запуск: " + " ".join(ov_args))
        self.vpn = subprocess.Popen(ov_args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace",
                                    creationflags=NO_WINDOW)
        threading.Thread(target=self._pump, args=(self.vpn, "openvpn"), daemon=True).start()
        threading.Thread(target=self._mgmt_loop, args=(mport, self.vpn, p), daemon=True).start()

        t0 = time.time()
        next_beat = t0 + 5
        while not self.up.wait(0.5):
            if self.vpn.poll() is not None:
                raise RuntimeError(f"OpenVPN завершился с кодом {self.vpn.returncode}")
            if self.ck.poll() is not None:
                raise RuntimeError(f"Cloak завершился с кодом {self.ck.returncode}")
            now = time.time()
            if now > t0 + 60:
                raise TimeoutError("OpenVPN не вышел в CONNECTED за 60 с "
                                   f"(последнее состояние: {self.last_state or 'нет данных'})")
            if now >= next_beat:
                next_beat += 5
                self.say(f"…жду OpenVPN {int(now - t0)} с; management: "
                         f"{'подключён' if self.mgmt else 'нет'}; состояние: {self.last_state or 'нет'}")

        self.active = p
        if self.data.get("last_profile") != p["name"]:
            self.data["last_profile"] = p["name"]
            save_data(self.data)
        self._start_monitor()
        time.sleep(1)
        if self.route_failed:
            self.say("ВНИМАНИЕ: OpenVPN не смог добавить маршруты (Access is denied) — трафик не идёт "
                     "через VPN. Запустите программу от имени администратора.")
            self.set_status("Маршруты не добавлены — нужны права администратора", "red")
        else:
            self.say("Подключено.")

    def _mgmt_loop(self, port, proc, p):
        sock = None
        for _ in range(50):
            if proc.poll() is not None:
                return
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                time.sleep(0.2)
        if sock is None:
            self.say("Не удалось подключиться к management-интерфейсу OpenVPN.")
            return
        sock.settimeout(None)
        self.mgmt = sock
        self.say("management подключён.")
        f = sock.makefile("rw", encoding="utf-8", errors="replace", newline="\n")
        try:
            f.write("state on all\n")
            f.flush()
            for line in f:
                line = line.strip()
                m = STATE_RE.match(line)
                if m:
                    self._on_state(m.group(1), p)
                elif line.startswith(">PASSWORD:"):
                    self.say("OpenVPN запрашивает логин/пароль — это пока не поддерживается: " + line)
                elif line.startswith(">FATAL:"):
                    self.say("OpenVPN: " + line)
                elif line:
                    self.say("mgmt: " + line)
        except (OSError, ValueError) as e:
            self.say(f"management: ошибка чтения {e!r}")
        self.say("management-соединение закрыто.")

    def _on_state(self, state, p):
        self.last_state = state
        self.say(f"Состояние OpenVPN: {state}")
        text, color = STATES.get(state, (state, "orange"))
        self.set_status(text.format(name=p["name"]), color)
        if state == "CONNECTED":
            self.up.set()

    def _start_monitor(self):
        stop = threading.Event()
        self._mon_stop = stop
        threading.Thread(target=self._monitor, args=(stop,), daemon=True).start()

    def _monitor(self, stop):
        warned = set()
        while not stop.wait(2):
            if self.ck and self.ck.poll() is not None and "ck" not in warned:
                warned.add("ck")
                self.say(f"Cloak остановился (код {self.ck.returncode}) — VPN не работает.")
                self.set_status("Cloak остановлен — VPN не работает", "red")
            if self.vpn and self.vpn.poll() is not None and "vpn" not in warned:
                warned.add("vpn")
                self.say(f"OpenVPN завершился (код {self.vpn.returncode}).")
                self.set_status("OpenVPN завершился", "red")

    # ---------- отключение ----------
    def _disconnect(self):
        self.say("Отключаюсь…")
        self._stop_all()
        self._check_loopback()
        self.say("Отключено.")
        self.set_status("Отключено", "gray")

    def _check_loopback(self):
        if repair_loopback():
            self.say("После отключения маршрут 127.0.0.1 пропал — восстановлен.")
        if not loopback_ok():
            self.say("ВНИМАНИЕ: 127.0.0.1 перестал отвечать — таблица маршрутов повреждена. "
                     "Остальные программы (например v2rayN) могут не работать до перезагрузки.")

    def _stop_all(self):
        if self._mon_stop:
            self._mon_stop.set()
        self._stop_vpn()
        self._stop_ck()
        self.active = None

    def _stop_vpn(self):
        vpn = self.vpn
        if vpn and vpn.poll() is None:
            try:
                if self.mgmt:
                    self.mgmt.sendall(b"signal SIGTERM\n")  # штатное завершение
            except OSError:
                pass
            try:
                vpn.wait(10)
            except subprocess.TimeoutExpired:
                vpn.terminate()
                try:
                    vpn.wait(5)
                except subprocess.TimeoutExpired:
                    vpn.kill()
        self.vpn = None
        try:
            if self.mgmt:
                self.mgmt.close()
        except OSError:
            pass
        self.mgmt = None

    def _stop_ck(self):
        if self.ck and self.ck.poll() is None:
            self.ck.terminate()
            try:
                self.ck.wait(5)
            except subprocess.TimeoutExpired:
                self.ck.kill()
        self.ck = None

    def _quit(self):
        self._stop_all()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
