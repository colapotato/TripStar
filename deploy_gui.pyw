#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
旅途星辰 (TripStar) — 一键部署与启动图形工具
双击运行即可，无需任何命令行操作。
"""

import os
import sys
import subprocess
import threading
import queue
import webbrowser
import time
import re
import json
from pathlib import Path
from tkinter import (
    Tk, Frame, Button, Text, END, NORMAL, DISABLED,
    messagebox, scrolledtext, font, Label, Canvas,
    NW, BOTH, LEFT, RIGHT, X, Y, TOP, BOTTOM, SOLID, GROOVE
)
from datetime import datetime

# ---------- 配置 ----------
PROJECT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_DIR = PROJECT_DIR / "frontend"

# spinner 动画字符
SPINNER = ['◐', '◓', '◑', '◒']

# 各操作的步骤定义
DEPLOY_STEPS = [
    ("check_env",        "检查系统环境"),
    ("install_backend_npm", "安装后端 Node.js 依赖"),
    ("create_venv",      "创建 Python 虚拟环境"),
    ("install_python_deps", "安装 Python 依赖包"),
    ("config_backend_env",  "配置后端环境变量"),
    ("install_frontend_npm", "安装前端依赖"),
    ("config_frontend_env", "配置前端环境变量"),
]

START_STEPS = [
    ("check_backend",    "检查后端部署状态"),
    ("check_frontend",   "检查前端部署状态"),
    ("check_api_keys",   "检查 API Key 配置"),
    ("start_backend",    "启动后端服务"),
    ("start_frontend",   "启动前端服务"),
    ("open_browser",     "打开浏览器"),
]


# ============================================================
# 核心逻辑 — 步骤化的工作线程
# ============================================================
class DeployWorker:
    def __init__(self, step_callback, log_callback):
        """
        step_callback(action, step_id, desc, detail='')
          action: 'start' | 'success' | 'fail' | 'skip'
        log_callback(msg, level)
          level: 'info' | 'ok' | 'warn' | 'error'
        """
        self.step_cb = step_callback
        self.log_cb = log_callback
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    # ---------- 工具 ----------
    def _run_cmd(self, cmd, cwd=None, timeout=300):
        if self._stop.is_set():
            return False
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd or PROJECT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in iter(proc.stdout.readline, ""):
                if self._stop.is_set():
                    proc.kill()
                    return False
                line = line.rstrip()
                if line:
                    self.log_cb(line, "info")
            proc.wait(timeout=timeout)
            return proc.returncode == 0
        except Exception as e:
            self.log_cb(f"命令执行异常: {e}", "error")
            return False

    def _which(self, name):
        if sys.platform == "win32":
            r = subprocess.run(["where", name], capture_output=True, text=True)
        else:
            r = subprocess.run(["which", name], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    # ---------- 部署 ----------
    def deploy(self):
        # --- step 1: 检查环境 ---
        self.step_cb("start", "check_env", "检查系统环境")
        env_ok, python_cmd, use_uv = self._check_env_impl()
        if not env_ok:
            self.step_cb("fail", "check_env", "检查系统环境", "系统环境不满足要求")
            return False
        self.step_cb("success", "check_env", "检查系统环境")

        # --- step 2: 后端 npm ---
        self.step_cb("start", "install_backend_npm", "安装后端 Node.js 依赖")
        if (BACKEND_DIR / "node_modules").exists():
            self.log_cb("backend/node_modules 已存在，跳过", "ok")
            self.step_cb("skip", "install_backend_npm", "安装后端 Node.js 依赖", "已存在，跳过")
        else:
            self.log_cb("正在安装...", "info")
            npm = "npm.cmd" if sys.platform == "win32" else "npm"
            ok = self._run_cmd([npm, "install"], cwd=BACKEND_DIR, timeout=180)
            if not ok:
                self.log_cb("npm install 失败，正在重试（使用离线模式）...", "warn")
                ok = self._run_cmd([npm, "install", "--prefer-offline", "--no-audit", "--no-fund"], cwd=BACKEND_DIR, timeout=180)
            if not ok:
                self.step_cb("fail", "install_backend_npm", "安装后端 Node.js 依赖", "npm install 失败，请检查网络连接")
                return False
            self.step_cb("success", "install_backend_npm", "安装后端 Node.js 依赖")

        # --- step 3: 创建虚拟环境 ---
        self.step_cb("start", "create_venv", "创建 Python 虚拟环境")
        if (BACKEND_DIR / ".venv").exists():
            self.log_cb("backend/.venv 已存在，跳过", "ok")
            self.step_cb("skip", "create_venv", "创建 Python 虚拟环境", "已存在，跳过")
        else:
            self.log_cb("正在创建虚拟环境...", "info")
            # 多级修复链：uv → python -m venv → virtualenv
            ok = False

            # 尝试 1: uv venv
            if use_uv:
                self.log_cb("  尝试方式 1/3: uv venv .venv", "info")
                ok = self._run_cmd(["uv", "venv", ".venv", "--clear"], cwd=BACKEND_DIR, timeout=120)

            # 尝试 2: python -m venv
            if not ok:
                self.log_cb("  尝试方式 2/3: python -m venv", "warn")
                ok = self._run_cmd([python_cmd, "-m", "venv", ".venv", "--clear"], cwd=BACKEND_DIR, timeout=120)

            # 尝试 3: virtualenv（先 pip 安装）
            if not ok:
                self.log_cb("  尝试方式 3/3: 安装 virtualenv 并创建", "warn")
                # 先用系统 python 安装 virtualenv
                self._run_cmd([python_cmd, "-m", "pip", "install", "virtualenv", "--user"],
                              cwd=BACKEND_DIR, timeout=120)
                ok = self._run_cmd([python_cmd, "-m", "virtualenv", ".venv"],
                                   cwd=BACKEND_DIR, timeout=120)

            if not ok:
                # 输出详细诊断信息帮助用户排查
                self.log_cb("  ── 诊断信息 ──", "error")
                self._run_cmd([python_cmd, "--version"], cwd=BACKEND_DIR, timeout=10)
                self._run_cmd([python_cmd, "-c", "import venv; print('venv 模块可用')"],
                              cwd=BACKEND_DIR, timeout=10)
                self.step_cb("fail", "create_venv", "创建 Python 虚拟环境",
                             "三种方式均失败，请尝试手动创建：python -m venv backend/.venv")
                return False
            self.step_cb("success", "create_venv", "创建 Python 虚拟环境")

        # --- step 4: 安装 Python 依赖 ---
        self.step_cb("start", "install_python_deps", "安装 Python 依赖包")
        self.log_cb("正在安装 Python 依赖包（可能需要几分钟）...", "info")

        # 准备安装命令列表（按优先级从高到低）
        install_attempts = []
        if use_uv:
            install_attempts.append(["uv", "pip", "install", "-r", "requirements.txt"])
            install_attempts.append(["uv", "pip", "install", "--no-build-isolation", "-r", "requirements.txt"])
        # pip 兜底
        if sys.platform == "win32":
            pip_exe = str(BACKEND_DIR / ".venv" / "Scripts" / "pip.exe")
        else:
            pip_exe = str(BACKEND_DIR / ".venv" / "bin" / "pip")
        install_attempts.append([pip_exe, "install", "-r", "requirements.txt"])
        install_attempts.append([pip_exe, "install", "--no-build-isolation", "-r", "requirements.txt"])

        ok = False
        for i, cmd in enumerate(install_attempts):
            if i > 0:
                self.log_cb(f"重试方式 {i + 1}/{len(install_attempts)}...", "warn")
            if self._run_cmd(cmd, cwd=BACKEND_DIR, timeout=600):
                ok = True
                break

        if not ok:
            self.step_cb("fail", "install_python_deps", "安装 Python 依赖包", "所有安装方式均失败，见日志")
            return False
        self.step_cb("success", "install_python_deps", "安装 Python 依赖包")

        # --- step 5: 后端 .env ---
        self.step_cb("start", "config_backend_env", "配置后端环境变量")
        env_file = BACKEND_DIR / ".env"
        example = BACKEND_DIR / ".env.example"
        if env_file.exists():
            self.log_cb("backend/.env 已存在", "ok")
            self.step_cb("skip", "config_backend_env", "配置后端环境变量", "已存在")
        elif example.exists():
            env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            self.log_cb("已创建 backend/.env，请编辑填入 API Key", "warn")
            self.step_cb("success", "config_backend_env", "配置后端环境变量", "请编辑填入 API Key")
        else:
            self.step_cb("fail", "config_backend_env", "配置后端环境变量", ".env.example 未找到")
            return False

        # --- step 6: 前端 npm ---
        self.step_cb("start", "install_frontend_npm", "安装前端依赖")
        if (FRONTEND_DIR / "node_modules").exists():
            self.log_cb("frontend/node_modules 已存在，跳过", "ok")
            self.step_cb("skip", "install_frontend_npm", "安装前端依赖", "已存在，跳过")
        else:
            self.log_cb("正在安装前端依赖...", "info")
            npm = "npm.cmd" if sys.platform == "win32" else "npm"
            ok = self._run_cmd([npm, "install"], cwd=FRONTEND_DIR, timeout=180)
            if not ok:
                self.log_cb("npm install 失败，正在重试（使用离线模式）...", "warn")
                ok = self._run_cmd([npm, "install", "--prefer-offline", "--no-audit", "--no-fund"], cwd=FRONTEND_DIR, timeout=180)
            if not ok:
                self.step_cb("fail", "install_frontend_npm", "安装前端依赖", "npm install 失败，请检查网络连接")
                return False
            self.step_cb("success", "install_frontend_npm", "安装前端依赖")

        # --- step 7: 前端 .env ---
        self.step_cb("start", "config_frontend_env", "配置前端环境变量")
        front_env = FRONTEND_DIR / ".env"
        front_example = FRONTEND_DIR / ".env.example"
        if front_env.exists():
            self.log_cb("frontend/.env 已存在", "ok")
            self.step_cb("skip", "config_frontend_env", "配置前端环境变量", "已存在")
        elif front_example.exists():
            front_env.write_text(front_example.read_text(encoding="utf-8"), encoding="utf-8")
            self.log_cb("已创建 frontend/.env，请编辑填入 API Key", "warn")
            self.step_cb("success", "config_frontend_env", "配置前端环境变量", "请编辑填入 API Key")
        else:
            self.step_cb("fail", "config_frontend_env", "配置前端环境变量", ".env.example 未找到")
            return False

        self.log_cb("", "info")
        self.log_cb("══════ 部署完成！请配置 API Key 后启动 ══════", "ok")
        return True

    # ---------- 启动 ----------
    def start(self):
        # --- step 1: 检查后端 ---
        self.step_cb("start", "check_backend", "检查后端部署状态")
        backend_ok = True
        checks_b = [
            (BACKEND_DIR / ".venv",         "后端虚拟环境 (.venv)"),
            (BACKEND_DIR / "node_modules",   "后端 Node.js 依赖"),
            (BACKEND_DIR / ".env",           "后端配置文件"),
        ]
        for path, desc in checks_b:
            if path.exists():
                self.log_cb(f"  ✓ {desc}", "ok")
            else:
                self.log_cb(f"  ✗ {desc} 未找到", "error")
                backend_ok = False
        if backend_ok:
            self.step_cb("success", "check_backend", "检查后端部署状态")
        else:
            detail = self._missing_hint([p for p, _ in checks_b])
            self.step_cb("fail", "check_backend", "检查后端部署状态", detail)
            return "need_deploy"

        # --- step 2: 检查前端 ---
        self.step_cb("start", "check_frontend", "检查前端部署状态")
        front_ok = True
        checks_f = [
            (FRONTEND_DIR / "node_modules",  "前端依赖"),
            (FRONTEND_DIR / ".env",          "前端配置文件"),
        ]
        for path, desc in checks_f:
            if path.exists():
                self.log_cb(f"  ✓ {desc}", "ok")
            else:
                self.log_cb(f"  ✗ {desc} 未找到", "error")
                front_ok = False
        if front_ok:
            self.step_cb("success", "check_frontend", "检查前端部署状态")
        else:
            detail = self._missing_hint([p for p, _ in checks_f])
            self.step_cb("fail", "check_frontend", "检查前端部署状态", detail)
            return "need_deploy"

        # --- step 3: 检查 API Key ---
        self.step_cb("start", "check_api_keys", "检查 API Key 配置")
        key_ok = self._check_env_keys_impl()
        if key_ok:
            self.step_cb("success", "check_api_keys", "检查 API Key 配置")
        else:
            self.step_cb("fail", "check_api_keys", "检查 API Key 配置",
                         "部分 Key 未配置，部分功能可能不可用")

        # --- step 4: 启动后端 ---
        self.step_cb("start", "start_backend", "启动后端服务")
        port = self._get_backend_port()
        self.log_cb(f"后端端口: {port}", "info")
        if sys.platform == "win32":
            venv_python = str(BACKEND_DIR / ".venv" / "Scripts" / "python.exe")
        else:
            venv_python = str(BACKEND_DIR / ".venv" / "bin" / "python")
        backend_cmd = [
            venv_python, "-m", "uvicorn", "app.api.main:app",
            "--host", "0.0.0.0", "--port", str(port), "--reload",
        ]
        kwargs = {"cwd": str(BACKEND_DIR), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            subprocess.Popen(backend_cmd, **kwargs)
            self.log_cb("后端服务已启动", "ok")
            self.step_cb("success", "start_backend", "启动后端服务")
        except Exception as e:
            self.log_cb(f"后端启动失败: {e}，正在重试...", "warn")
            try:
                subprocess.Popen(backend_cmd, **kwargs)
                self.log_cb("后端服务已启动（重试成功）", "ok")
                self.step_cb("success", "start_backend", "启动后端服务")
            except Exception as e2:
                self.log_cb(f"后端启动重试仍然失败: {e2}", "error")
                self.step_cb("fail", "start_backend", "启动后端服务", str(e2))
                return False

        # 等待后端就绪
        self.log_cb("等待后端就绪...", "info")
        time.sleep(3)

        # --- step 5: 启动前端 ---
        self.step_cb("start", "start_frontend", "启动前端服务")
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        kwargs = {"cwd": str(FRONTEND_DIR), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            subprocess.Popen([npm, "run", "dev"], **kwargs)
            self.log_cb("前端服务已启动", "ok")
            self.step_cb("success", "start_frontend", "启动前端服务")
        except Exception as e:
            self.log_cb(f"前端启动失败: {e}，正在重试...", "warn")
            try:
                subprocess.Popen([npm, "run", "dev"], **kwargs)
                self.log_cb("前端服务已启动（重试成功）", "ok")
                self.step_cb("success", "start_frontend", "启动前端服务")
            except Exception as e2:
                self.log_cb(f"前端启动重试仍然失败: {e2}", "error")
                self.step_cb("fail", "start_frontend", "启动前端服务", str(e2))
                return False

        # --- step 6: 记下端口，由 GUI 询问用户后决定是否打开浏览器 ---
        self.step_cb("start", "open_browser", "打开浏览器")
        self.log_cb(f"前端地址: http://localhost:5173", "ok")
        self.log_cb(f"后端文档: http://localhost:{port}/docs", "info")
        # step 6 保持 running，GUI 在询问用户后决定 success / skip
        return ("started", port)

    # ---------- 内部辅助 ----------
    def _check_env_impl(self):
        self.log_cb("检查 Python...", "info")
        python_cmd = None
        for cmd in ("python3", "python"):
            path = self._which(cmd)
            if path:
                try:
                    ver = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=10)
                    self.log_cb(f"  ✓ {ver.stdout.strip() or ver.stderr.strip()}", "ok")
                    python_cmd = cmd
                    break
                except Exception:
                    continue
        if not python_cmd:
            self.log_cb("  ✗ Python 未找到！请安装 Python 3.10+", "error")
            return False, None, False

        self.log_cb("检查 Node.js...", "info")
        if self._which("node"):
            ver = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10)
            self.log_cb(f"  ✓ Node.js {ver.stdout.strip()}", "ok")
        else:
            self.log_cb("  ✗ Node.js 未找到！请安装 Node.js 18+", "error")
            return False, None, False

        use_uv = self._which("uv") is not None
        if use_uv:
            self.log_cb("  ✓ uv 已安装（将加速 Python 依赖安装）", "ok")
        else:
            self.log_cb("  ○ uv 未安装，正在自动安装...", "warn")
            ok = self._run_cmd([python_cmd, "-m", "pip", "install", "uv", "--quiet"],
                               cwd=BACKEND_DIR, timeout=120)
            if ok:
                self.log_cb("  ✓ uv 安装成功", "ok")
                use_uv = True
            else:
                self.log_cb("  ○ uv 自动安装失败（将使用 pip 作为备选）", "warn")

        return True, python_cmd, use_uv

    def _check_env_keys_impl(self):
        keys_to_check = [
            ("LLM_API_KEY",          BACKEND_DIR / ".env"),
            ("VITE_AMAP_WEB_KEY",   BACKEND_DIR / ".env"),
            ("XHS_COOKIE",          BACKEND_DIR / ".env"),
            ("VITE_AMAP_WEB_JS_KEY", FRONTEND_DIR / ".env"),
        ]
        all_ok = True
        placeholders = ["your_key", "your_url", "your_model", "your_amap",
                        "your_xhs", "your_", "key_here"]
        for key_name, env_file in keys_to_check:
            if not env_file.exists():
                self.log_cb(f"  ○ {env_file.name} 不存在，跳过 Key 检查", "warn")
                continue
            try:
                content = env_file.read_text(encoding="utf-8")
                match = re.search(rf"^{re.escape(key_name)}=(.+)$", content, re.MULTILINE)
                if match:
                    value = match.group(1).strip().strip('"').strip("'")
                    is_ph = any(p in value.lower() for p in placeholders)
                    is_empty = not value
                    if is_empty or is_ph:
                        self.log_cb(f"  ○ {key_name} 未配置（仍为占位符）", "warn")
                        all_ok = False
                    else:
                        masked = value[:10] + "..." if len(value) > 10 else value
                        self.log_cb(f"  ✓ {key_name} 已配置", "ok")
                else:
                    self.log_cb(f"  ○ {key_name} 未在 .env 中找到", "warn")
                    all_ok = False
            except Exception:
                pass
        return all_ok

    def _get_backend_port(self):
        env_file = BACKEND_DIR / ".env"
        if env_file.exists():
            try:
                m = re.search(r"^PORT=(\d+)", env_file.read_text(encoding="utf-8"), re.MULTILINE)
                if m:
                    return int(m.group(1))
            except Exception:
                pass
        return 8000

    def _missing_hint(self, paths):
        missing = [p.name for p in paths if not p.exists()]
        return f"缺失: {', '.join(missing)}" if missing else "请先部署项目"


# ============================================================
# GUI 组件 — 步骤指示面板
# ============================================================
class StepPanel(Frame):
    """带圆点指示器的步骤列表组件"""
    COLORS = {
        "pending": "#555",
        "running": "#4fc3f7",
        "success": "#4caf50",
        "fail":    "#f44336",
        "skip":    "#888",
        "bg":      "#12121f",
        "text":    "#ccc",
        "text_light": "#777",
    }

    def __init__(self, master, steps, **kwargs):
        super().__init__(master, bg=self.COLORS["bg"], **kwargs)
        self._steps = steps          # [(id, desc), ...]
        self._indicators = {}        # step_id -> StringVar (for spinner)
        self._status = {}            # step_id -> 'pending'|'running'|'success'|'fail'|'skip'
        self._desc_labels = {}       # step_id -> Label
        self._detail_labels = {}     # step_id -> Label
        self._spinner_jobs = {}      # step_id -> after job id
        self._spinner_idx = {}       # step_id -> current index
        self._rows = {}              # step_id -> Frame

        self._build()

    def _build(self):
        for i, (step_id, desc) in enumerate(self._steps):
            row = Frame(self, bg=self.COLORS["bg"])
            row.pack(fill=X, pady=(0 if i == 0 else 2, 0))
            self._rows[step_id] = row

            # 圆点指示器 Label
            indicator = Label(row, text="○", font=("Consolas", 14),
                              fg=self.COLORS["pending"], bg=self.COLORS["bg"],
                              width=2, anchor="center")
            indicator.pack(side=LEFT, padx=(8, 4))
            self._indicators[step_id] = indicator

            # 步骤描述（文本可能变化）
            desc_label = Label(row, text=desc, font=("Microsoft YaHei", 10),
                               fg=self.COLORS["text"], bg=self.COLORS["bg"],
                               anchor="w")
            desc_label.pack(side=LEFT, fill=X, expand=True)
            self._desc_labels[step_id] = desc_label

            # 详情文字（右侧，用于显示小提示）
            detail_label = Label(row, text="", font=("Microsoft YaHei", 8),
                                 fg=self.COLORS["text_light"], bg=self.COLORS["bg"],
                                 anchor="e")
            detail_label.pack(side=RIGHT, padx=(4, 8))
            self._detail_labels[step_id] = detail_label

            self._status[step_id] = "pending"
            self._spinner_idx[step_id] = 0

    def set_step(self, step_id, status, detail=""):
        """更新某个步骤的状态和详情文字"""
        if step_id not in self._indicators:
            return

        # 停止旧的 spinner
        if step_id in self._spinner_jobs and self._spinner_jobs[step_id]:
            try:
                self.after_cancel(self._spinner_jobs[step_id])
            except Exception:
                pass
            self._spinner_jobs[step_id] = None

        indicator = self._indicators[step_id]
        desc_label = self._desc_labels[step_id]
        detail_label = self._detail_labels[step_id]
        self._status[step_id] = status

        color_map = {
            "pending": self.COLORS["pending"],
            "running": self.COLORS["running"],
            "success": self.COLORS["success"],
            "fail":    self.COLORS["fail"],
            "skip":    self.COLORS["skip"],
        }
        char_map = {
            "pending": "○",
            "running": "◌",
            "success": "●",
            "fail":    "●",
            "skip":    "○",
        }
        color = color_map.get(status, self.COLORS["pending"])
        indicator.config(text=char_map.get(status, "○"), fg=color)

        if status == "success":
            desc_label.config(fg=self.COLORS["success"])
            indicator.config(text="✓", fg=self.COLORS["success"])
        elif status == "fail":
            desc_label.config(fg=self.COLORS["fail"])
            indicator.config(text="✗", fg=self.COLORS["fail"])
        elif status == "skip":
            indicator.config(text="→", fg=self.COLORS["skip"])
            desc_label.config(fg=self.COLORS["text"])
        elif status == "running":
            desc_label.config(fg=self.COLORS["running"])
            # 启动 spinner
            self._spinner_idx[step_id] = 0
            self._animate_spinner(step_id)
        else:  # pending
            desc_label.config(fg=self.COLORS["text"])

        if detail:
            detail_label.config(text=detail)
        else:
            detail_label.config(text="")

    def _animate_spinner(self, step_id):
        """spinner 动画帧"""
        idx = self._spinner_idx.get(step_id, 0)
        char = SPINNER[idx % len(SPINNER)]
        indicator = self._indicators.get(step_id)
        if indicator:
            indicator.config(text=char, fg=self.COLORS["running"])
        self._spinner_idx[step_id] = idx + 1
        # 如果状态仍然是 running，继续动画
        if self._status.get(step_id) == "running":
            self._spinner_jobs[step_id] = self.after(120, self._animate_spinner, step_id)

    def reset_all(self):
        """将所有步骤重置为 pending"""
        for step_id in self._indicators:
            # 停止 spinner
            if step_id in self._spinner_jobs and self._spinner_jobs[step_id]:
                try:
                    self.after_cancel(self._spinner_jobs[step_id])
                except Exception:
                    pass
                self._spinner_jobs[step_id] = None
            self.set_step(step_id, "pending")


# ============================================================
# GUI 主界面
# ============================================================
class DeployGUI:
    COLORS = {
        "bg":        "#0f0f1a",
        "panel_bg":  "#1a1a2e",
        "accent":    "#16213e",
        "text":      "#e0e0e0",
        "text_dim":  "#888",
        "deploy":    "#27ae60",
        "deploy_h":  "#2ecc71",
        "start":     "#2980b9",
        "start_h":   "#3498db",
        "danger":    "#e74c3c",
        "log_bg":    "#0a0a14",
    }

    def __init__(self):
        self.root = Tk()
        self.root.title("旅途星辰 TripStar — 部署与启动工具")
        self.root.geometry("780x680")
        self.root.minsize(680, 580)
        self.root.configure(bg=self.COLORS["bg"])

        # 设置图标
        try:
            icon_path = PROJECT_DIR / "frontend" / "favicon.png"
            if icon_path.exists():
                img = __import__("tkinter").PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, img)
        except Exception:
            pass

        self._busy = False
        self._worker = None
        self._msg_queue = queue.Queue()

        self._build_ui()
        self._poll_queue()

        # 居中
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ---------- UI 构建 ----------
    def _build_ui(self):
        root = self.root

        # === 标题 ===
        title_frame = Frame(root, bg=self.COLORS["bg"])
        title_frame.pack(fill=X, padx=24, pady=(18, 4))
        Label(title_frame, text="旅途星辰 TripStar",
              font=("Microsoft YaHei", 20, "bold"),
              fg="#e8e8ff", bg=self.COLORS["bg"]).pack(anchor="w")
        Label(title_frame, text="一键部署与启动工具",
              font=("Microsoft YaHei", 10),
              fg=self.COLORS["text_dim"], bg=self.COLORS["bg"]).pack(anchor="w")

        # === 步骤面板（由后续操作动态创建） ===
        self.step_container = Frame(root, bg=self.COLORS["bg"])
        self.step_container.pack(fill=BOTH, expand=True, padx=24, pady=(12, 0))

        # 占位文字（初始状态）
        self.placeholder = Label(
            self.step_container,
            text="点击下方按钮开始操作\n\n"
                 "「🚀 部署项目」— 完整安装依赖与环境配置\n"
                 "「▶  启动项目」— 检查部署状态并启动服务",
            font=("Microsoft YaHei", 10),
            fg=self.COLORS["text_dim"], bg=self.COLORS["bg"],
            justify="center",
        )
        self.placeholder.pack(expand=True)

        self.step_panel = None  # 后面动态创建

        # === 按钮栏 ===
        btn_frame = Frame(root, bg=self.COLORS["bg"])
        btn_frame.pack(fill=X, padx=24, pady=(8, 4))

        self.deploy_btn = self._mkbtn(btn_frame, " 🚀  部署项目  ",
                                       self.COLORS["deploy"], self.COLORS["deploy_h"],
                                       self._on_deploy)
        self.deploy_btn.pack(side=LEFT, padx=(0, 10))

        self.start_btn = self._mkbtn(btn_frame, " ▶  启动项目  ",
                                      self.COLORS["start"], self.COLORS["start_h"],
                                      self._on_start)
        self.start_btn.pack(side=LEFT)

        # 底部状态标签 + 清空按钮
        bottom_bar = Frame(root, bg=self.COLORS["bg"])
        bottom_bar.pack(fill=X, padx=24, pady=(2, 0))

        self.status_label = Label(
            bottom_bar, text="就绪", font=("Microsoft YaHei", 9),
            fg=self.COLORS["text_dim"], bg=self.COLORS["bg"], anchor="w",
        )
        self.status_label.pack(side=LEFT, fill=X, expand=True)

        self.clear_btn = self._mkbtn(bottom_bar, " 🗑  清空日志  ",
                                      "#555", "#666", self._on_clear, font_size=9, padx=10, pady=3)
        self.clear_btn.pack(side=RIGHT)

        # === 日志栏 ===
        log_frame = Frame(root, bg="#0a0a14", highlightbackground="#1a1a2e",
                          highlightthickness=1)
        log_frame.pack(fill=BOTH, padx=24, pady=(6, 16), ipady=0)

        # 日志标题
        log_header = Frame(log_frame, bg="#0f0f1a")
        log_header.pack(fill=X)
        Label(log_header, text="📋 运行日志",
              font=("Microsoft YaHei", 8, "bold"),
              fg=self.COLORS["text_dim"], bg="#0f0f1a").pack(anchor="w", padx=8, pady=4)

        self.log_text = Text(
            log_frame,
            bg=self.COLORS["log_bg"],
            fg="#a0a0b0",
            font=("Consolas", 9),
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=4,
            wrap="word",
            state=DISABLED,
            height=8,
        )
        self.log_text.pack(fill=BOTH, expand=True)

        # 滚动条
        scrollbar = __import__("tkinter").Scrollbar(self.log_text)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.log_text.yview)

        # 日志 tag
        self.log_text.tag_config("ok",    foreground="#4caf50")
        self.log_text.tag_config("warn",  foreground="#ff9800")
        self.log_text.tag_config("error", foreground="#f44336")
        self.log_text.tag_config("info",  foreground="#a0a0b0")

    def _mkbtn(self, parent, text, color, hover_color, command,
               font_size=12, padx=18, pady=9):
        btn = Button(
            parent, text=text,
            font=("Microsoft YaHei", font_size, "bold"),
            fg="white", bg=color,
            activeforeground="white", activebackground=hover_color,
            relief="flat", borderwidth=0, cursor="hand2",
            padx=padx, pady=pady, command=command,
        )

        def on_enter(e, c=hover_color):
            if not self._busy:
                e.widget.config(bg=c)
        def on_leave(e, c=color):
            e.widget.config(bg=c)
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    # ---------- 步骤面板管理 ----------
    def _switch_to_steps(self, steps, scroll_to_end=True):
        """切换到某组步骤面板"""
        if self.step_panel:
            self.step_panel.pack_forget()
            self.step_panel.destroy()
        self.placeholder.pack_forget()

        self.step_panel = StepPanel(self.step_container, steps)
        self.step_panel.pack(fill=BOTH, expand=True)
        return self.step_panel

    # ---------- 日志 ----------
    def _log(self, text, level="info"):
        self._msg_queue.put(("log", (text, level)))

    def _step_cb_from_thread(self, action, step_id, desc, detail=""):
        """从工作线程发来的步骤回调"""
        self._msg_queue.put(("step", (action, step_id, desc, detail)))

    def _poll_queue(self):
        try:
            while True:
                msg_type, data = self._msg_queue.get_nowait()
                if msg_type == "log":
                    text, level = data
                    self._do_log(text, level)
                elif msg_type == "step":
                    action, step_id, desc, detail = data
                    if self.step_panel:
                        self.step_panel.set_step(step_id, action, detail)
                        if action == "running":
                            self.set_status(f"▶ {desc}...", "running")
                        elif action == "success":
                            self.set_status(f"✓ {desc}", "success")
                        elif action == "fail":
                            self.set_status(f"✗ {desc}", "fail")
                elif msg_type == "status":
                    text, level = data
                    self.set_status(text, level)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def _do_log(self, text, level="info"):
        self.log_text.config(state=NORMAL)
        ts = datetime.now().strftime("%H:%M:%S")
        if text:
            self.log_text.insert(END, f"[{ts}] {text}\n", level)
        self.log_text.see(END)
        self.log_text.config(state=DISABLED)

    def set_status(self, text, level="info"):
        color_map = {
            "success": self.COLORS["deploy"],
            "fail":    self.COLORS["danger"],
            "running": self.COLORS["start"],
            "info":    self.COLORS["text_dim"],
        }
        self.status_label.config(text=text, fg=color_map.get(level, self.COLORS["text_dim"]))

    # ---------- 按钮回调 ----------
    def _set_busy(self, busy):
        self._busy = busy
        state = DISABLED if busy else NORMAL
        self.deploy_btn.config(state=state)
        self.start_btn.config(state=state)
        self.root.update()

    def _run_worker(self, mode):
        """mode: 'deploy' or 'start'"""
        if self._busy:
            return

        self._set_busy(True)
        self.log_text.config(state=NORMAL)
        self.log_text.delete(1.0, END)
        self.log_text.config(state=DISABLED)

        steps = DEPLOY_STEPS if mode == "deploy" else START_STEPS
        self._switch_to_steps(steps)

        def _run():
            worker = DeployWorker(
                step_callback=self._step_cb_from_thread,
                log_callback=self._log,
            )
            if mode == "deploy":
                success = worker.deploy()
                self._msg_queue.put(("status", (
                    "✅ 部署完成！请配置 API Key 后点击「启动项目」" if success
                    else "❌ 部署失败，请查看日志",
                    "success" if success else "fail"
                )))
            else:
                result = worker.start()
                if result == "need_deploy":
                    self._msg_queue.put(("status", ("⚠ 部署不完整", "fail")))
                    self.root.after(0, self._ask_deploy)
                elif isinstance(result, tuple) and result[0] == "started":
                    port = result[1]
                    self._msg_queue.put(("status", (
                        "✅ 服务已启动！", "success")))
                    self.root.after(0, self._ask_browser, port)
                else:
                    self._msg_queue.put(("status", (
                        "❌ 启动失败，请查看日志", "fail")))
            self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=_run, daemon=True).start()

    def _ask_browser(self, port):
        """询问是否启动浏览器"""
        answer = messagebox.askyesno(
            "启动浏览器",
            "服务已启动！是否打开浏览器访问？\n\n"
            f"前端地址: http://localhost:5173\n"
            f"后端文档: http://localhost:{port}/docs",
            icon="question",
        )
        if answer:
            webbrowser.open("http://localhost:5173")
            if self.step_panel:
                self.step_panel.set_step("open_browser", "success",
                                          "http://localhost:5173")
            self.set_status("✅ 服务已启动，浏览器已打开", "success")
        else:
            if self.step_panel:
                self.step_panel.set_step("open_browser", "skip", "用户已取消")
            self.set_status("✅ 服务已启动，浏览器未打开 (可手动访问)", "info")

    def _ask_deploy(self):
        answer = messagebox.askyesno(
            "项目尚未部署",
            "检测到项目尚未完成部署，是否立即部署？\n\n"
            "选择「是」→ 自动安装所有依赖\n"
            "选择「否」→ 返回主界面",
            icon="question",
        )
        if answer:
            self._run_worker("deploy")
        else:
            self.set_status("已取消 — 部署后再来启动吧", "info")

    def _is_deployed(self):
        """快速检查部署状态（不输出日志）"""
        checks = [
            BACKEND_DIR / ".venv",
            BACKEND_DIR / "node_modules",
            BACKEND_DIR / ".env",
            FRONTEND_DIR / "node_modules",
            FRONTEND_DIR / ".env",
        ]
        return all(p.exists() for p in checks)

    def _on_deploy(self):
        if self._is_deployed():
            messagebox.showinfo(
                "已部署",
                "项目已完成部署，无需重复部署。\n\n"
                "可直接点击「启动项目」运行。",
            )
            return
        self._run_worker("deploy")

    def _on_start(self):
        # 先检查部署状态，未部署则询问
        if not self._is_deployed():
            answer = messagebox.askyesno(
                "未部署",
                "检测到项目尚未部署，是否立即部署？\n\n"
                "选择「是」→ 自动安装所有依赖\n"
                "选择「否」→ 取消操作",
                icon="question",
            )
            if answer:
                self._run_worker("deploy")
            else:
                self.set_status("已取消操作", "info")
            return
        self._run_worker("start")

    def _on_clear(self):
        self.log_text.config(state=NORMAL)
        self.log_text.delete(1.0, END)
        self.log_text.config(state=DISABLED)
        self.set_status("日志已清空", "info")

    def run(self):
        self.root.mainloop()


# ============================================================
# 入口
# ============================================================
def main():
    os.chdir(str(PROJECT_DIR))
    try:
        gui = DeployGUI()
        gui.run()
    except ImportError:
        print("错误: tkinter 不可用。")
        print("  Windows: 重新安装 Python 时勾选 'tcl/tk and IDLE'")
        print("  Linux:   sudo apt install python3-tk")
        print("  macOS:   brew install python-tk")
        input("\n按 Enter 退出...")
        sys.exit(1)


if __name__ == "__main__":
    main()
