import os
import sys
import time
import json
import base64
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext

# --- 关键修改 1: 显式导入底层模块 ---
import selenium.webdriver.chrome.webdriver
# ------------------------------------

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import subprocess
import pystray
from PIL import Image
import traceback

LOGIN_URL = "https://net.szu.edu.cn"
CHECK_INTERVAL = 10
CREDENTIAL_FILE = "cred.dat"
ICON_FILE = "szu.ico"

# ================= 关键修改 2: 路径分离逻辑 =================
def get_bundled_resource_path(relative_path):
    """ 获取打包进 exe 内部的资源文件路径 (例如 szu.ico) """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller 打包后，内置文件会被解压到 sys._MEIPASS 这个临时目录
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# 获取 exe 所在的外部真实目录 (用于寻找外置的 chromedriver.exe)
if getattr(sys, 'frozen', False):
    EXTERNAL_DIR = os.path.dirname(sys.executable)
else:
    EXTERNAL_DIR = os.path.dirname(os.path.abspath(__file__))

CHROMEDRIVER_PATH = os.path.join(EXTERNAL_DIR, "chromedriver.exe") # 驱动必须放在 exe 同级目录
ICON_PATH = get_bundled_resource_path(ICON_FILE)                   # 图标从 exe 内部读取
# ==============================================================

# ... [后面的 AutoLoginApp 类及主程序代码保持完全不变] ...
# ===== GUI 主窗口 =====
class AutoLoginApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("深圳大学校园网自动登录")
        self.geometry("450x400")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

        # 用户名
        tk.Label(self, text="用户名:").place(x=20, y=20)
        self.username_var = tk.StringVar()
        self.entry_username = tk.Entry(self, textvariable=self.username_var, width=30)
        self.entry_username.place(x=120, y=20)

        # 密码
        tk.Label(self, text="密码:").place(x=20, y=60)
        self.password_var = tk.StringVar()
        self.entry_password = tk.Entry(self, textvariable=self.password_var, width=30, show="*")
        self.entry_password.place(x=120, y=60)

        # 显示密码勾选
        self.show_pwd_var = tk.IntVar()
        self.chk_show_pwd = tk.Checkbutton(self, text="显示密码", variable=self.show_pwd_var, command=self.toggle_password)
        self.chk_show_pwd.place(x=350, y=55)

        # 登录 & 清除按钮
        self.btn_login = tk.Button(self, text="登录", width=10, command=self.start_login)
        self.btn_login.place(x=120, y=100)
        self.btn_clear = tk.Button(self, text="清除", width=10, command=self.clear_inputs)
        self.btn_clear.place(x=250, y=100)

        # 开机自启勾选
        self.auto_start_var = tk.IntVar()
        self.chk_auto_start = tk.Checkbutton(self, text="开机自启", variable=self.auto_start_var)
        self.chk_auto_start.place(x=20, y=100)

        # 日志显示框
        tk.Label(self, text="日志:").place(x=20, y=150)
        self.log_text = scrolledtext.ScrolledText(self, width=52, height=12, state='disabled')
        self.log_text.place(x=20, y=180)

        self.running = False
        self.thread = None
        self.tray_icon = None

        # 加载账号密码
        self.load_credentials()

    def toggle_password(self):
        self.entry_password.config(show="" if self.show_pwd_var.get() else "*")

    def clear_inputs(self):
        self.username_var.set("")
        self.password_var.set("")

    def load_credentials(self):
        if os.path.exists(CREDENTIAL_FILE):
            with open(CREDENTIAL_FILE, "rb") as f:
                data = base64.b64decode(f.read()).decode()
                creds = json.loads(data)
                self.username_var.set(creds.get("username", ""))
                self.password_var.set(creds.get("password", ""))

    def save_credentials(self):
        data = {"username": self.username_var.get(), "password": self.password_var.get()}
        with open(CREDENTIAL_FILE, "wb") as f:
            f.write(base64.b64encode(json.dumps(data).encode()))

    def log(self, msg):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')} {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def set_inputs_state(self, state: bool):
        state_val = 'normal' if state else 'disabled'
        self.entry_username.config(state=state_val)
        self.entry_password.config(state=state_val)
        self.btn_login.config(state=state_val)
        self.btn_clear.config(state=state_val)
        self.chk_show_pwd.config(state=state_val)
        self.chk_auto_start.config(state=state_val)

    def start_login(self):
        if not self.username_var.get() or not self.password_var.get():
            messagebox.showwarning("提示", "请输入用户名和密码")
            return
        self.save_credentials()
        if self.auto_start_var.get():
            self.set_autostart()
        self.set_inputs_state(False)
        self.running = True
        self.thread = threading.Thread(target=self.run_login_loop, daemon=True)
        self.thread.start()

    def ping(self, host):
        try:
            result = subprocess.run(
                ["ping", "-n", "1", host],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return result.returncode == 0
        except:
            return False

    # ===== Selenium 4+ 登录 =====
    def perform_login(self):
        if not os.path.exists(CHROMEDRIVER_PATH):
            messagebox.showerror("错误", "缺少 chromedriver.exe")
            self.log("chromedriver.exe 缺失")
            return False
        try:
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            service = Service(CHROMEDRIVER_PATH)
            driver = webdriver.Chrome(service=service, options=options)
            driver.get(LOGIN_URL)
            wait = WebDriverWait(driver, 10)
            user_input = wait.until(EC.presence_of_element_located((By.ID, "username")))
            pwd_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='password']")))
            login_btn = wait.until(EC.element_to_be_clickable((By.ID, "login-account")))
            user_input.clear()
            user_input.send_keys(self.username_var.get())
            pwd_input.clear()
            pwd_input.send_keys(self.password_var.get())
            login_btn.click()
            time.sleep(5)
            driver.quit()
            self.log("登录执行完成")
            return True
        except Exception as e:
            self.log(f"登录异常: {e}")
            return False

    def run_login_loop(self):
        while self.running:
            if self.ping("114.114.114.114") or self.ping("8.8.4.4"):
                self.log("网络正常")
            else:
                self.log("网络断开，尝试登录...")
                success = self.perform_login()
                if not success:
                    self.set_inputs_state(True)
            time.sleep(CHECK_INTERVAL)

    def set_autostart(self):
        startup_dir = os.path.join(os.environ["APPDATA"], "Microsoft\\Windows\\Start Menu\\Programs\\Startup")
        bat_path = os.path.join(startup_dir, "campus_auto_login.bat")
        exe_path = getattr(sys, 'frozen', False) and sys.executable or os.path.abspath(__file__)
        with open(bat_path, "w") as f:
            f.write(f'@echo off\nstart "" "{exe_path}"')
        self.log("已设置开机自启")

    # ===== 托盘相关 =====
    def hide_window(self):
        self.withdraw()
        if self.tray_icon is None:
            image = Image.open(ICON_PATH)
            menu = pystray.Menu(
                pystray.MenuItem('显示窗口', lambda icon, item: self.show_window()),
                pystray.MenuItem('登录', lambda icon, item: threading.Thread(target=self.perform_login, daemon=True).start()),
                pystray.MenuItem('退出', lambda icon, item: self.exit_app(icon))
            )
            self.tray_icon = pystray.Icon("校园网自动登录", image, "校园网登录", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_window(self):
        self.deiconify()

    def exit_app(self, icon):
        self.running = False
        icon.stop()
        self.destroy()


if __name__ == "__main__":
    app = AutoLoginApp()
    app.mainloop()
