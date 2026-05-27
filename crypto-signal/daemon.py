#!/usr/bin/env python3
"""
daemon.py — 模拟交易定时更新守护进程（Phase C1）
每60秒调用 paper_trader.cmd_update() 更新持仓 P&L + 检查 TP/SL。
用法:
  python3 daemon.py start   # 启动守护进程（后台）
  python3 daemon.py stop    # 停止守护进程
  python3 daemon.py status  # 查看状态
  python3 daemon.py run      # 前台运行（调试）
"""

import json, time, signal, sys, pathlib as pl, subprocess, os, datetime
from typing import Optional

# ── 常量 ─────────────────────────────────────────────────────────────────────
BASE       = pl.Path(__file__).parent
PID_FILE   = BASE / ".daemon.pid"
LOG_FILE   = BASE / "daemon.log"
INTERVAL   = 60   # 更新间隔（秒）
MODULE     = "paper_trader.py"

# ── 日志 ───────────────────────────────────────────────────────────────────────
def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    print(line.rstrip())  # 同时输出到 stdout
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ── 单次更新 ──────────────────────────────────────────────────────────────────
def update_once() -> bool:
    """调用 paper_trader.cmd_update()，返回是否成功"""
    try:
        result = subprocess.run(
            [sys.executable, MODULE, "update"],
            cwd=str(BASE),
            capture_output=True,
            timeout=60,
        )
        if result.returncode == 0:
            log(f"✅ 更新成功")
            return True
        else:
            err = result.stderr.decode("utf-8", errors="replace")[:200]
            log(f"⚠️ 更新失败（返回码 {result.returncode}）: {err}")
            return False
    except subprocess.TimeoutExpired:
        log("⚠️ 更新超时（>60s），跳过本次")
        return False
    except Exception as e:
        log(f"❌ 更新异常: {e}")
        return False


# ── 守护进程主循环 ───────────────────────────────────────────────────────
def daemon_loop():
    """每 INTERVAL 秒执行一次更新"""
    log(f"🚀 守护进程启动（间隔 {INTERVAL}s）")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT,  lambda *_: sys.exit(0))

    while True:
        try:
            update_once()
        except SystemExit:
            log("🛑 收到终止信号，退出")
            break
        except Exception as e:
            log(f"❌ 主循环异常: {e}")

        # 等待下一个周期
        time.sleep(INTERVAL)


# ── PID 文件管理 ───────────────────────────────────────────────────────────
def write_pid():
    PID_FILE.write_text(str(os.getpid()))


def read_pid() -> Optional[int]:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def remove_pid():
    if PID_FILE.exists():
        PID_FILE.unlink()


# ── 进程控制 ───────────────────────────────────────────────────────────────
def start_daemon():
    """启动后台守护进程"""
    pid = read_pid()
    if pid and _is_process_running(pid):
        print(f"⚠️ 守护进程已在运行（PID {pid}）")
        return

    log("🚀 启动后台守护进程...")
    # 使用 nohup 后台运行
    cmd = f"nohup {sys.executable} {__file__} run > /dev/null 2>&1 &"
    os.system(cmd)
    time.sleep(2)
    print(f"✅ 守护进程已启动（查看日志: tail -f {LOG_FILE}）")


def stop_daemon():
    """停止守护进程"""
    pid = read_pid()
    if not pid:
        print("⚠️ 未找到运行中的守护进程")
        return
    if not _is_process_running(pid):
        print(f"⚠️ PID {pid} 已不在运行，清理 PID 文件")
        remove_pid()
        return
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        remove_pid()
        print(f"✅ 守护进程已停止（PID {pid}）")
    except Exception as e:
        print(f"❌ 停止失败: {e}")


def status_daemon():
    """查看守护进程状态"""
    pid = read_pid()
    if not pid:
        print("⚠️ 守护进程未运行（无 PID 文件）")
        return
    if _is_process_running(pid):
        print(f"✅ 守护进程运行中（PID {pid}）")
        if LOG_FILE.exists():
            print(f"   日志: {LOG_FILE}（tail -20）")
            os.system(f"tail -20 {LOG_FILE}")
    else:
        print(f"⚠️ PID {pid} 不在运行，清理残留 PID 文件")
        remove_pid()


def _is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # 不发送信号，仅检查进程是否存在
        return True
    except (OSError, ProcessLookupError):
        return False


# ── 入口 ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "run":
        # 前台运行（调试模式）
        write_pid()
        try:
            daemon_loop()
        finally:
            remove_pid()
    elif args[0] == "start":
        start_daemon()
    elif args[0] == "stop":
        stop_daemon()
    elif args[0] == "status":
        status_daemon()
    else:
        print(f"未知命令: {args[0]}")
        print("用法: python3 daemon.py [start|stop|status|run]")
        sys.exit(1)
