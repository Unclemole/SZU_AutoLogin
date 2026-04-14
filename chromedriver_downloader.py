import json
import os
import threading
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
from urllib import error, request
import zipfile

try:
    import winreg
except ImportError:
    winreg = None


DOWNLOAD_TEMPLATE = (
    "https://storage.googleapis.com/chrome-for-testing-public/"
    "{version}/win64/chromedriver-win64.zip"
)
LATEST_PATCH_API = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "latest-patch-versions-per-build.json"
)
LATEST_VERSIONS_API = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "latest-versions-per-milestone.json"
)


def detect_chrome_version():
    if winreg is None:
        return None

    key_paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon"),
    ]

    for root, path in key_paths:
        try:
            with winreg.OpenKey(root, path) as key:
                version, _ = winreg.QueryValueEx(key, "version")
                if isinstance(version, str) and version.strip():
                    return version.strip()
        except OSError:
            continue
    return None


def fetch_json(url):
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_download_version(detected_version):
    if not detected_version:
        return None

    # 1) Use exact detected version first.
    exact_url = DOWNLOAD_TEMPLATE.format(version=detected_version)
    if url_exists(exact_url):
        return detected_version

    # 2) Fallback to latest patch in same build (major.minor.build).
    parts = detected_version.split(".")
    if len(parts) >= 3:
        build_key = ".".join(parts[:3])
        try:
            data = fetch_json(LATEST_PATCH_API)
            builds = data.get("builds", {})
            entry = builds.get(build_key)
            if entry and entry.get("version"):
                return entry["version"]
        except Exception:
            pass

    # 3) Fallback to latest in same milestone (major).
    major = detected_version.split(".")[0]
    try:
        data = fetch_json(LATEST_VERSIONS_API)
        milestones = data.get("milestones", {})
        entry = milestones.get(major)
        if entry and entry.get("version"):
            return entry["version"]
    except Exception:
        pass

    return None


def url_exists(url):
    req = request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with request.urlopen(req, timeout=10):
            return True
    except Exception:
        return False


def download_file(url, target_path):
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with request.urlopen(req, timeout=30) as resp, open(target_path, "wb") as f:
        while True:
            chunk = resp.read(1024 * 64)
            if not chunk:
                break
            f.write(chunk)


def extract_chromedriver(zip_path, output_dir):
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        driver_member = None
        for name in names:
            if name.endswith("chromedriver.exe"):
                driver_member = name
                break

        if not driver_member:
            raise RuntimeError("压缩包中未找到 chromedriver.exe")

        zf.extract(driver_member, output_dir)
        extracted_path = os.path.join(output_dir, driver_member)
        final_path = os.path.join(output_dir, "chromedriver.exe")

        if os.path.abspath(extracted_path) != os.path.abspath(final_path):
            if os.path.exists(final_path):
                os.remove(final_path)
            os.replace(extracted_path, final_path)

        # Clean temporary folders from extraction (chromedriver-win64/*)
        top_dir = driver_member.split("/")[0] if "/" in driver_member else None
        if top_dir:
            temp_dir = os.path.join(output_dir, top_dir)
            if os.path.isdir(temp_dir):
                try:
                    for root, dirs, files in os.walk(temp_dir, topdown=False):
                        for file_name in files:
                            os.remove(os.path.join(root, file_name))
                        for dir_name in dirs:
                            os.rmdir(os.path.join(root, dir_name))
                    os.rmdir(temp_dir)
                except Exception:
                    pass
    return final_path


class DownloaderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ChromeDriver 一键下载器")
        self.geometry("520x220")
        self.resizable(False, False)

        self.detected_version = detect_chrome_version()

        container = ttk.Frame(self, padding=20)
        container.pack(fill="both", expand=True)

        self.version_label = ttk.Label(
            container,
            text=f"Chrome版本：{self.detected_version or '未检测到'}",
            font=("Microsoft YaHei UI", 12),
        )
        self.version_label.pack(anchor="w", pady=(0, 16))

        self.status_var = tk.StringVar(value="准备就绪。")
        self.status_label = ttk.Label(
            container,
            textvariable=self.status_var,
            foreground="#475569",
            font=("Microsoft YaHei UI", 10),
        )
        self.status_label.pack(anchor="w", pady=(0, 16))

        self.download_btn = ttk.Button(
            container,
            text="一键下载",
            command=self.on_download,
            width=18,
        )
        self.download_btn.pack(anchor="w")

        self.path_label = ttk.Label(
            container,
            text=f"保存目录：{os.path.abspath('.')}",
            foreground="#64748b",
            font=("Microsoft YaHei UI", 9),
        )
        self.path_label.pack(anchor="w", pady=(16, 0))

    def on_download(self):
        if not self.detected_version:
            messagebox.showerror("错误", "未检测到 Chrome 版本，请确认已安装 Chrome。")
            return
        self.download_btn.config(state="disabled")
        self.status_var.set("正在解析可用版本...")
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        try:
            version = resolve_download_version(self.detected_version)
            if not version:
                raise RuntimeError("无法匹配可下载的 ChromeDriver 版本。")

            url = DOWNLOAD_TEMPLATE.format(version=version)
            zip_path = os.path.abspath("chromedriver-win64.zip")
            self._set_status(f"开始下载：{version}")
            download_file(url, zip_path)

            self._set_status("下载完成，正在解压...")
            final_path = extract_chromedriver(zip_path, os.path.abspath("."))
            self._set_status(f"完成：{final_path}")
            messagebox.showinfo(
                "完成",
                f"ChromeDriver 下载并解压成功。\n版本：{version}\n路径：{final_path}",
            )
        except Exception as e:
            self._set_status("下载失败。")
            messagebox.showerror("失败", str(e))
        finally:
            self.download_btn.config(state="normal")

    def _set_status(self, text):
        self.after(0, lambda: self.status_var.set(text))


if __name__ == "__main__":
    app = DownloaderApp()
    app.mainloop()
