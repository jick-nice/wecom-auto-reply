# -*- coding: utf-8 -*-
"""打包脚本：把 client_v33 打成单文件 exe。

用法：
    python build.py                      # 用当前 endpoint 地址打包
    python build.py https://xxx.vercel.app/api   # 指定后端地址后打包

做的事情：
    ① 把 app/endpoint.pyc 里的 API_BASE 替换成目标地址（备份原文件）
    ② 跑 selftest，不通过就中止
    ③ PyInstaller onefile + noconsole 打包
    ④ 校验产物
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
CLIENT = ROOT / "client_v33"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ENDPOINT = CLIENT / "app" / "endpoint.pyc"
ICON = ROOT / "assets" / "autograb.ico"
PY = sys.executable


def patch_endpoint(url: str) -> None:
    """把 endpoint.pyc 里的 API_BASE 常量替换成 url。

    pyc 里的字符串常量是 UTF-8 字节串，长度变了不能直接替换（会影响
    marshal 结构）。做法：**重新生成整个 endpoint 模块的 pyc** —— 写一个
    新的 endpoint.py（含新地址），用同版本 Python 编译成 pyc
    （code object 的 magic 与解释器一致，直接可用）。
    """
    py = ENDPOINT.with_suffix(".py")
    bak = ENDPOINT.with_suffix(".pyc.bak")

    if not bak.exists():
        shutil.copy2(ENDPOINT, bak)

    src = f'''# -*- coding: utf-8 -*-
"""后端地址的唯一出口。

**要求：客户端界面里不显示、也不允许用户修改后端地址。**
所以地址只存在于这一个文件的常量里，界面上只有账号和密码两个输入框。

换地址（部署 / 换域名）只改下面这一行，然后重新打包：

    API_BASE = "https://你的域名/api"

本地联调用环境变量临时覆盖，不必改代码、也不会出现在界面上：

    $env:AUTOGRAB_API = "http://127.0.0.1:3100"
    python client\\main.py

为什么不做成"读配置 / 界面上填"：一旦地址可编辑，用户就能把它指到别处，
也能直接看到后端域名 —— 这两件事都不该由终端用户决定。
"""
from __future__ import annotations

import os

API_BASE = "{url}"
ENV_KEY = "AUTOGRAB_API"


def resolve() -> str:
    """返回当前生效的后端地址（已去掉结尾斜杠）。"""
    return ((os.environ.get(ENV_KEY) or API_BASE or "").strip().rstrip("/"))


def is_placeholder() -> bool:
    """是否还停在开发地址上（打包前自检用，避免发出去连不上）。"""
    u = resolve()
    return (not u) or u.startswith("http://127.0.0.1") or ("REPLACE-ME" in u)
'''
    py.write_text(src, encoding="utf-8")

    # 编译成 pyc 覆盖（同版本 Python，magic 一致）
    import py_compile
    py_compile.compile(str(py), cfile=str(ENDPOINT), doraise=True)
    py.unlink()          # 不留 .py，避免优先级冲突
    print(f"[1/4] 后端地址已写入: {url}")


def run_selftest() -> bool:
    print("[2/4] 运行自检…")
    r = subprocess.run([PY, "main.py", "--selftest"], cwd=CLIENT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print(out)
    bad = [ln for ln in out.splitlines() if "[FAIL]" in ln]
    if bad or r.returncode != 0:
        print("!! 自检失败，中止打包")
        return False
    return True


def build() -> Path | None:
    print("[3/4] PyInstaller 打包…")
    for d in (DIST, BUILD):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    args = [
        PY, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir", "--noconsole",
        "--name", "抢单助手",
        "--icon", str(ICON),
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        # 托盘
        "--hidden-import", "pystray",
        "--hidden-import", "pystray._win32",
        "--hidden-import", "app.captureguard",
        # OCR 模型必须整包收进去
        "--collect-all", "rapidocr_onnxruntime",
        "--collect-all", "onnxruntime",
        # OCR 使用 ONNX Runtime 推理，无需导出/训练框架。
        "--exclude-module", "torch",
        "--exclude-module", "tensorflow",
        "--hidden-import", "PIL._tkinter_finder",
        # 固定手势和数字模板随包发布，避免依赖开发机的 APPDATA。
        "--add-data", str(CLIENT / "assets") + os.pathsep + "assets",
        str(CLIENT / "main.py"),
    ]
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print((r.stdout or "")[-3000:])
        print((r.stderr or "")[-3000:])
        print("!! 打包失败")
        return None

    exe = DIST / "抢单助手" / "抢单助手.exe"
    if not exe.exists():
        print("!! 没找到产物")
        return None
    return exe


def verify(exe: Path) -> bool:
    print("[4/4] 校验…")
    size = sum(p.stat().st_size for p in exe.parent.rglob("*") if p.is_file())
    print(f"    产物: {exe}")
    print(f"    目录总大小: {size/1024/1024:.1f} MB")
    if size < 30 * 1024 * 1024:
        print("    !! 体积过小，OCR 模型可能没打进去")
        return False
    if not (exe.parent / "_internal").exists():
        print("    !! 缺少 PyInstaller 运行时目录")
        return False
    return True


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else None
    if not url:
        # 读现有地址
        sys.path.insert(0, str(CLIENT))
        import importlib.machinery
        m = importlib.machinery.SourcelessFileLoader(
            "ep", str(ENDPOINT)).load_module("ep")
        url = m.resolve()
        if m.is_placeholder():
            print(f"当前地址是开发地址({url})。请指定生产地址：")
            print("  python build.py https://xxx.vercel.app/api")
            return 2

    patch_endpoint(url)
    if not run_selftest():
        return 3
    exe = build()
    if not exe:
        return 4
    if not verify(exe):
        return 5
    print("\n完成:", exe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())