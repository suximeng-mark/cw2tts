"""插件打包脚本（替代 cw-plugin-pack）。

为什么不用官方的 cw-plugin-pack：
官方打包器在 Windows runner 上执行失败（v1.0.2 连续多次 CI 在
"Build plugin packages" 步骤失败，而同一次运行里我们自己的
"pip install --target" 预演步骤成功 —— 说明依赖可正常安装，
问题出在打包器本身）。

本脚本按官方打包器的行为重实现：
1. 校验 cwplugin.json 与入口文件
2. 复制源码到临时构建目录（应用同样的忽略规则）
3. pip install --target libs/ 把依赖装进包里
4. 打包为 .cwplugin（zip 格式）

额外增强：
- 安装输出通过 subprocess.run 完整捕获，不会因管道缓冲阻塞
- 打包后校验关键依赖是否真的在包内，缺失即失败
- 临时目录与日志在退出前清理，避免混入发布包

用法：
    python build_pack.py            # 生成 com.su.tts.cwplugin
    python build_pack.py --format zip
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

# CI 步骤摘要路径：把失败原因写进去，避免只能靠受限的日志 API 排查
SUMMARY_PATH = os.environ.get("GITHUB_STEP_SUMMARY")
# CI 里同时写一份本地日志，便于作为 artifact 上传后取证
LOG_PATH = os.environ.get("BUILD_PACK_LOG")


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8。

    Windows runner 上 Python 的标准输出默认是 cp1252，一旦输出中文
    （如「源目录」「打包失败」）就会抛 UnicodeEncodeError。
    本地控制台多为 UTF-8，所以此问题只在 CI 暴露。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_force_utf8_stdio()


def report(line: str) -> None:
    """输出到 stdout，并在 CI 中同时写入步骤摘要与本地日志。"""
    print(line, flush=True)
    for path in (SUMMARY_PATH, LOG_PATH):
        if not path:
            continue
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line.rstrip() + "\n")
        except OSError:
            pass


def report_block(title: str, body: str) -> None:
    report(f"\n### {title}\n```\n{body.rstrip()[-6000:]}\n```\n")


# 与官方 SDK PluginPackager.DEFAULT_IGNORE_PATTERNS 保持一致
DEFAULT_IGNORE_PATTERNS = {
    "__pycache__", "*.pyc", "*.pyo", "*.pyd",
    ".DS_Store", "Thumbs.db", "desktop.ini",
    ".git", ".gitignore", ".gitattributes",
    ".venv", "venv", "env", ".idea", ".vscode",
    "*.cwplugin", "*.zip",
    "dist", "build", "*.egg-info",
    "cwplugin.local.json",
    # 本插件打包流程的临时产物
    "_preflight_libs", "_preflight.log", "_pack.log", "_pack2.log", "*.log",
    "_build_pack.log",
}

# 发布包内必须存在的依赖（前缀匹配）
REQUIRED_IN_PACKAGE = {
    "onnxruntime": "libs/onnxruntime/capi/",
    "edge-tts": "libs/edge_tts/__init__.py",
    "winrt-runtime": "libs/winrt/",
    "class-widgets-sdk": "libs/ClassWidgets/",
    "pypinyin": "libs/pypinyin/__init__.py",
}


def step(msg: str) -> None:
    report(f"  ➜ {msg}")


def info(key: str, value: str) -> None:
    report(f"    {key:<20} {value}")


def resolve_dependencies(source_dir: Path) -> list[str]:
    """读取依赖声明：优先 pyproject.toml，其次 requirements.txt。"""
    toml_path = source_dir / "pyproject.toml"
    if toml_path.exists():
        try:
            if sys.version_info >= (3, 11):
                import tomllib
                with open(toml_path, "rb") as f:
                    data = tomllib.load(f)
            else:
                import tomli
                with open(toml_path, "rb") as f:
                    data = tomli.load(f)
            deps = data.get("project", {}).get("dependencies", [])
            if deps:
                info("来源", "pyproject.toml")
                return deps
        except Exception as e:
            print(f"    ⚠️  解析 pyproject.toml 失败: {e}")

    req_path = source_dir / "requirements.txt"
    if req_path.exists():
        deps = [
            line.strip()
            for line in req_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if deps:
            info("来源", "requirements.txt")
            return deps
    return []


def ignore_filter(_src, names):
    ignored = set()
    for name in names:
        for pattern in DEFAULT_IGNORE_PATTERNS:
            if fnmatch.fnmatch(name, pattern):
                ignored.add(name)
                break
    return ignored


def copy_source(source_dir: Path, build_dir: Path) -> None:
    step("复制源码...")
    shutil.copytree(source_dir, build_dir, ignore=ignore_filter, dirs_exist_ok=True)


def install_dependencies(deps: list[str], lib_dir: Path) -> None:
    if not deps:
        step("无须安装依赖。")
        return

    step(f"正在安装 {len(deps)} 个依赖到 libs/ ...")
    lib_dir.mkdir(exist_ok=True)

    cmd = [
        sys.executable, "-m", "pip", "install",
        *deps,
        "--target", str(lib_dir),
        "--no-python-version-warning",
        "--disable-pip-version-check",
    ]
    # 不用 Popen + poll 轮询：pip 输出量大时管道缓冲会填满导致死锁。
    # subprocess.run 内部持续读取直到进程结束，不会阻塞。
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=1800)
    if result.returncode != 0:
        report_block("pip install 失败（依赖安装）", result.stdout + "\n" + result.stderr)
        raise RuntimeError(f"pip install 失败，退出码 {result.returncode}")

    # 清理字节码，进一步缩小体积
    removed = 0
    for item in lib_dir.rglob("*"):
        if item.suffix in (".pyc", ".pyo"):
            item.unlink()
            removed += 1
    info("清理字节码", f"{removed} 个")

    installed = len(list(lib_dir.glob("*-info")))
    info("已安装", f"{installed} packages")


def verify_package(output_path: Path) -> None:
    step("校验包内依赖...")
    names = set(zipfile.ZipFile(output_path).namelist())
    missing = [k for k, v in REQUIRED_IN_PACKAGE.items()
               if not any(n.startswith(v) for n in names)]
    if missing:
        raise RuntimeError("发布包缺少依赖: " + ", ".join(missing))
    info("依赖校验", "全部齐全 (" + ", ".join(REQUIRED_IN_PACKAGE) + ")")


def create_zip(build_dir: Path, output_path: Path) -> None:
    step(f"正在打包 {output_path.name} ...")
    files = [f for f in build_dir.rglob("*") if f.is_file()]
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, path in enumerate(files, 1):
            zf.write(path, path.relative_to(build_dir))
            if i % 200 == 0:
                report(f"      已压缩 {i}/{len(files)}")
    info("条目数", str(len(files)))


def main() -> int:
    parser = argparse.ArgumentParser(description="CW2 插件打包")
    parser.add_argument("plugin_dir", nargs="?", default=".")
    parser.add_argument("-o", "--output")
    parser.add_argument("-f", "--format", choices=["cwplugin", "zip"], default="cwplugin")
    args = parser.parse_args()

    source_dir = Path(args.plugin_dir).resolve()
    report("Class Widgets Plugin Packager (bundled build)")
    report("-" * 42)
    info("源目录", str(source_dir))
    info("输出格式", args.format)

    def fail(message: str) -> int:
        """输出错误并返回失败码，同时以 GitHub 注解形式暴露（可匿名读取）。"""
        report(f"❌ {message}")
        esc = message.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title=build_pack failed::{esc}")
        return 1

    manifest_path = source_dir / "cwplugin.json"
    if not manifest_path.exists():
        return fail("未找到 cwplugin.json，请在插件目录下运行此命令。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field in ("id", "name", "version", "entry"):
        if field not in manifest:
            return fail(f"清单缺少 '{field}'")
    entry_file = source_dir / manifest["entry"]
    if not entry_file.exists():
        return fail(f"入口文件不存在: {entry_file.name}")

    plugin_id = manifest["id"]
    suffix = f".{args.format}"
    output_path = (
        Path(args.output).resolve() if args.output
        else source_dir / f"{plugin_id}{suffix}"
    )

    print()
    step(f"验证插件... (id={plugin_id}, version={manifest['version']})")

    with tempfile.TemporaryDirectory(prefix=f"cw_build_{plugin_id}_") as tmp:
        build_dir = Path(tmp)
        copy_source(source_dir, build_dir)
        install_dependencies(resolve_dependencies(source_dir), build_dir / "libs")
        create_zip(build_dir, output_path)

    verify_package(output_path)

    report("")
    report("✅ 打包完成!")
    info("文件", str(output_path))
    info("大小", f"{output_path.stat().st_size / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - 顶层兜底，把真实原因写进步骤摘要
        tb = traceback.format_exc()
        report_block("打包失败（完整错误）", tb)
        traceback.print_exc()
        # 同时以 GitHub 注解形式输出：注解可通过 check-runs API 匿名读取，
        # 而 runs/{id}/logs 与 artifact 下载均需认证，便于无 token 时定位问题。
        esc = tb.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title=build_pack failed::{esc}")
        sys.exit(1)
