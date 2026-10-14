"""MiniMax 生成工具：出图（image-01）和出视频（MiniMax-H3）。

密钥从工作文件夹（运行命令时所在的文件夹）的 .env 或环境变量读取，不写进代码、不打印：
    MINIMAX_API_KEY=...
    MINIMAX_HOST=https://api.minimaxi.com   # 国内站；国际站用 https://api.minimax.io

用法：
    python mm.py check
    python mm.py image  --prompt-file p.txt --out 目录 [--n 4] [--ratio 9:16] [--ref 角色图.png]
    python mm.py video  --prompt-file p.txt --out 片段.mp4 [--first 首帧.png] [--last 尾帧.png] [--ref 设定图.png ...]
    注意：H3 规定 --first（首帧/尾帧）和 --ref（参考图）只能二选一，同时传会报 400（错误码 2013）。
                        [--duration 5] [--resolution 768P] [--ratio 9:16] [--model MiniMax-H3]
"""
import argparse, base64, json, mimetypes, os, sys, time, urllib.request, urllib.error

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.environ.get("TRYON_VLOG_WORKDIR") or os.getcwd())  # 工作文件夹，.env 放这里


def load_env():
    env = {}
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8-sig"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    key = env.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_API_KEY")
    host = (env.get("MINIMAX_HOST") or os.environ.get("MINIMAX_HOST") or "https://api.minimaxi.com").rstrip("/")
    if not key:
        sys.exit("未找到 MINIMAX_API_KEY：请在工作文件夹的 .env 里填写（参考 .env.example），或设置环境变量")
    return key, host


KEY, HOST = load_env()


def call(method, path, body=None, timeout=120):
    req = urllib.request.Request(HOST + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, {"raw": txt[:500]}


def data_url(path):
    mt = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mt};base64," + base64.b64encode(open(path, "rb").read()).decode()


def download(url, out):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    urllib.request.urlretrieve(url, out)
    return out


def cmd_check(a):
    # 用一个不存在的任务号查询：401 说明密钥或站点不对，其他说明密钥可用
    code, j = call("GET", "/v2/query/video_generation/0")
    print("HOST", HOST, "HTTP", code, json.dumps(j, ensure_ascii=False)[:300])


def cmd_image(a):
    body = {"model": "image-01", "prompt": open(a.prompt_file, encoding="utf-8").read().strip(),
            "aspect_ratio": a.ratio, "n": a.n, "response_format": "base64", "prompt_optimizer": False}
    if a.ref:
        body["subject_reference"] = [{"type": "character", "image_file": data_url(a.ref)}]
    code, j = call("POST", "/v1/image_generation", body, timeout=300)
    br = j.get("base_resp", {})
    if code != 200 or br.get("status_code", 0) != 0:
        sys.exit(f"出图失败 HTTP {code}: {json.dumps(j, ensure_ascii=False)[:500]}")
    os.makedirs(a.out, exist_ok=True)
    start = len([f for f in os.listdir(a.out) if f.endswith(".png")])
    for i, b in enumerate(j["data"].get("image_base64", []), start + 1):
        p = os.path.join(a.out, f"{a.prefix}{i:02d}.png")
        open(p, "wb").write(base64.b64decode(b))
        print(p)
    print("metadata", j.get("metadata"))


def cmd_video(a):
    content = [{"type": "text", "text": open(a.prompt_file, encoding="utf-8").read().strip()}]
    if a.first:
        content.append({"type": "image_url", "image_url": {"url": data_url(a.first)}, "role": "first_frame"})
    if a.last:
        content.append({"type": "image_url", "image_url": {"url": data_url(a.last)}, "role": "last_frame"})
    for r in a.ref or []:
        content.append({"type": "image_url", "image_url": {"url": data_url(r)}, "role": "reference_image"})
    body = {"model": a.model, "content": content, "duration": a.duration, "resolution": a.resolution, "ratio": a.ratio}
    if a.resume:  # 已提交过（已扣费）的任务：只查询下载，不重新提交
        tid = a.resume
    else:
        code, j = call("POST", "/v2/video_generation", body, timeout=300)
        tid = j.get("task_id")
        if code != 200 or not tid:
            sys.exit(f"提交失败 HTTP {code}: {json.dumps(j, ensure_ascii=False)[:500]}")
    print("task_id", tid, flush=True)
    if a.task_file:
        open(a.task_file, "w", encoding="utf-8").write(tid)
    t0 = time.time()
    while True:
        time.sleep(10)
        try:
            code, t = call("GET", f"/v2/query/video_generation/{tid}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:  # 网络抖动：任务还在服务端跑，继续查
            print(f"[{int(time.time()-t0)}s] 查询出错，重试：{e}", flush=True)
            continue
        st = t.get("status") or t.get("task", {}).get("status")
        print(f"[{int(time.time()-t0)}s] {st}", flush=True)
        if st == "succeeded":
            url = (t.get("content") or t.get("task", {}).get("content") or {}).get("url")
            for i in range(3):
                try:
                    print(download(url, a.out))
                    return
                except (urllib.error.URLError, TimeoutError, OSError) as e:
                    print(f"下载出错，重试：{e}", flush=True)
                    time.sleep(5)
            sys.exit(f"已生成但下载失败（已扣费，不要重新提交），用 --resume {tid} 再取")
        if st in ("failed", "cancelled"):
            sys.exit(f"生成失败：{json.dumps(t, ensure_ascii=False)[:500]}")
        if time.time() - t0 > 1800:
            sys.exit(f"等待超过 30 分钟（任务可能还在跑，已扣费，不要重新提交），用 --resume {tid} 再取")


p = argparse.ArgumentParser()
s = p.add_subparsers(dest="cmd", required=True)
s.add_parser("check").set_defaults(f=cmd_check)
i = s.add_parser("image"); i.set_defaults(f=cmd_image)
i.add_argument("--prompt-file", required=True); i.add_argument("--out", required=True)
i.add_argument("--n", type=int, default=4); i.add_argument("--ratio", default="9:16")
i.add_argument("--ref"); i.add_argument("--prefix", default="")
v = s.add_parser("video"); v.set_defaults(f=cmd_video)
v.add_argument("--prompt-file", required=True); v.add_argument("--out", required=True)
v.add_argument("--first"); v.add_argument("--last"); v.add_argument("--ref", action="append")
v.add_argument("--duration", type=int, default=5); v.add_argument("--resolution", default="768P")
v.add_argument("--ratio", default="9:16"); v.add_argument("--model", default="MiniMax-H3")
v.add_argument("--resume", help="已提交任务的 task_id：只查询下载，不重新提交（不再扣费）")
v.add_argument("--task-file", help="提交成功后把 task_id 写进这个文件")
a = p.parse_args(); a.f(a)
