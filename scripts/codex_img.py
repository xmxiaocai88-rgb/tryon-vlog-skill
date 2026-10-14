"""用 Codex CLI 自带的出图功能（走 ChatGPT 套餐额度，不需要 OpenAI API key）生成 1 张图。

用法：
    python -I codex_img.py --prompt-file 画面.txt --out 输出.png [--base 底图.png] [--ref 参考图.png ...] [--note 附图说明] [--ratio 9:16]
画面.txt 只写画面内容；本脚本负责包上「用图片生成功能、复制到指定路径」的指令。
--out 必须在工作文件夹（运行命令时所在的文件夹，或环境变量 TRYON_VLOG_WORKDIR）里面，因为 Codex 只能写这个文件夹。
每张约 2~3 分钟。
"""
import argparse, os, shutil, subprocess, sys

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.environ.get("TRYON_VLOG_WORKDIR") or os.getcwd())
p = argparse.ArgumentParser()
p.add_argument("--prompt-file", required=True); p.add_argument("--out", required=True)
p.add_argument("--ref", action="append", default=[]); p.add_argument("--ratio", default="9:16")
p.add_argument("--base", help="底图：以它为基础做图片编辑")
p.add_argument("--note", default="", help="附图说明：告诉出图模型每张附图是什么、能改什么")
a = p.parse_args()

out = os.path.relpath(os.path.abspath(a.out), ROOT).replace("\\", "/")
if out.startswith(".."):
    sys.exit(f"--out 必须在工作文件夹 {ROOT} 里面")
if os.path.exists(os.path.join(ROOT, out)):
    sys.exit(f"已存在，换个文件名：{out}")
os.makedirs(os.path.dirname(os.path.join(ROOT, out)), exist_ok=True)
scene = open(a.prompt_file, encoding="utf-8").read().strip()
task = f"""请使用你的图片生成功能（image generation）生成 1 张图片，比例 {a.ratio}。
{a.note}
不要写代码画图，不要修改任何已有文件。生成完成后，把生成的图片文件复制到：{out} ，最后只回复保存的文件路径。

画面描述：
{scene}
"""
codex = shutil.which("codex") or "codex"
cmd = [codex, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", ROOT]
if os.name == "nt":  # 部分 Windows 机器上默认的 elevated 沙箱初始化失败，这里只对本次调用改用 unelevated
    cmd[2:2] = ["-c", "windows.sandbox=unelevated"]
for r in ([a.base] if a.base else []) + a.ref:
    cmd += ["-i", r]
cmd += ["-"]
r = subprocess.run(cmd, input=task.encode("utf-8"), capture_output=True, timeout=900)
tail = r.stdout.decode("utf-8", "replace").strip().splitlines()[-3:]
if os.path.exists(os.path.join(ROOT, out)):
    print("OK", out)
else:
    sys.exit("生成失败：\n" + "\n".join(tail) + "\n" + r.stderr.decode("utf-8", "replace")[-800:])
