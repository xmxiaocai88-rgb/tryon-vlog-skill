"""换装 vlog 流水线：模特照 + 衣服照 → 换装照 → 手机 vlog 首帧（自拍 / 朋友跟拍）→ H3 6 秒视频 → 成片。
提示词全部由 templates/ 按 brief.json 拼出来，使用者只填 brief.json，不手写提示词。

用法（在你的工作文件夹下运行，那里放 .env 和素材；python 加 -I）：
  python -I <skill目录>/scripts/tryon_vlog.py init  <名称>      新建作品文件夹 + brief.json
  python -I ... lint   <作品文件夹>                 检查 brief.json（每次改完都要跑）
  python -I ... tryon  <作品文件夹>                 出换装照（不花钱）+ 审核图，最后打印 pick 命令
  python -I ... pick   <作品文件夹> tryon|frame <图片路径>   把合格的图定稿
  python -I ... frame  <作品文件夹>                 出 vlog 首帧（不花钱）+ 审核图，最后打印 pick 命令
  python -I ... video  <作品文件夹> --spend         出 H3 视频（花 1 次钱）+ 成片 + 审核图
  python -I ... video  <作品文件夹> --resume <task_id>   已扣费但没拿到视频时续取（不再扣费）
"""
import argparse, datetime, json, os, re, shutil, subprocess, sys

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ROOT = os.path.abspath(os.environ.get("TRYON_VLOG_WORKDIR") or os.getcwd())  # 工作文件夹：放 .env、素材和输出
VP = HERE
OUTBASE = os.path.join(ROOT, "output", "换装vlog")
ME = f'python -I "{os.path.abspath(__file__)}"'

FIELDS = ["name", "model_image", "garment_image", "camera", "person", "garment_items", "garment_short",
          "scene", "light", "start_pose", "action_1", "action_2"]
TEXT_FIELDS = ["person", "garment_items", "garment_short", "scene", "start_pose", "action_1", "action_2"]
# 这些词会把画面拉回「AI 精修感」，brief 里一律不许出现（模板里的否定句不受影响）。比较时统一转大写
BANNED = ["电影", "大片", "4K", "8K", "高清", "柔光", "光晕", "逆光", "夕阳", "黄昏", "霓虹", "焦外", "景深", "唯美", "完美",
          "精致", "梦幻", "网红", "高级感", "杂志", "慢动作", "特写", "美颜", "磨皮", "滤镜", "棚拍", "影棚", "布景", "黄金时刻"]
LIGHTS = ["普通的上午阳光直射（顶光偏侧面）", "阴天的柔和散射光", "室内窗边的自然光", "晚上路灯和店铺招牌的混合光"]
SEQ = ["然后再", "接着再", "然后", "接着", "随后", "之后", "最后", "再"]
CAMERAS = ["自拍", "朋友跟拍"]
SELFIE_ONLY = ["手机", "屏幕", "自拍", "前置"]   # 朋友跟拍时人物手里没有手机
FRIEND_ONLY = ["双手", "朋友"]                   # 自拍时一只手一直举着手机


def die(msg):
    sys.exit("❌ " + msg)


def ab(p):
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(ROOT, p))


def load(d):
    p = os.path.join(d, "brief.json")
    if not os.path.exists(p):
        die(f"找不到 {p}：作品文件夹写错了，或者还没运行 init")
    return json.load(open(p, encoding="utf-8"))


def log(d, line):
    with open(os.path.join(d, "记录.md"), "a", encoding="utf-8") as f:
        f.write(f"- {datetime.datetime.now():%Y-%m-%d %H:%M} {line}\n")


def nxt(d, sub, stem, ext):
    os.makedirs(os.path.join(d, sub), exist_ok=True)
    i = 1
    while os.path.exists(os.path.join(d, sub, f"{stem}-{i:02d}{ext}")):
        i += 1
    return os.path.join(d, sub, f"{stem}-{i:02d}{ext}"), f"{i:02d}"


def render(name, b):
    if name in ("frame.txt", "video.txt"):
        name = name.replace(".txt", f"-{b['camera']}.txt")
    t = open(os.path.join(SKILL, "templates", name), encoding="utf-8").read()
    return t.format(**dict(b, garment_desc="、".join(b["garment_items"])))


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def size(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", p], capture_output=True, text=True, check=True)
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def ratio_of(p):
    """模特照的比例，换成出图支持的最接近的一档（换装要保持原比例）"""
    w, h = size(p)
    opts = {"9:16": 9 / 16, "2:3": 2 / 3, "3:4": 3 / 4, "1:1": 1, "4:3": 4 / 3, "3:2": 3 / 2, "16:9": 16 / 9}
    return min(opts, key=lambda k: abs(opts[k] - w / h))


def lint_errors(b):
    errs = []
    for k in FIELDS:
        if not b.get(k):
            errs.append(f"{k} 没填")
    if errs:
        return errs
    if not isinstance(b["garment_items"], list) or not all(isinstance(x, str) and x for x in b["garment_items"]):
        return ["garment_items 必须是一个列表，每一条是一段文字，例如 [\"浅蓝色水洗牛仔外套\", \"翻领\"]"]
    for k in FIELDS:
        if k != "garment_items" and not isinstance(b[k], str):
            errs.append(f"{k} 必须是一段文字")
    if errs:
        return errs
    ex = json.load(open(os.path.join(SKILL, "brief.example.json"), encoding="utf-8"))
    for k in TEXT_FIELDS:
        if b[k] == ex[k]:
            errs.append(f"{k} 还是示例原文（牛仔外套那份）：要看这次的模特照 / 衣服照重新写")
    for k in ("model_image", "garment_image"):
        if not os.path.exists(ab(b[k])):
            errs.append(f"{k} 文件不存在：{b[k]}")
    if b["camera"] not in CAMERAS:
        errs.append(f"camera 只能填 {' 或 '.join(CAMERAS)}，现在是「{b['camera']}」")
    if b["light"] not in LIGHTS:
        errs.append("light 必须从这 4 个里原样复制一个：" + " / ".join(LIGHTS))
    if not 3 <= len(b["garment_items"]) <= 12:
        errs.append("garment_items 要 3~12 条：把衣服拆成单品和细节逐条写（颜色面料款式、领、扣、袖、图案、腰、长度）")
    for k in TEXT_FIELDS:
        text = ("、".join(b[k]) if isinstance(b[k], list) else b[k]).upper()
        for w in BANNED:
            if w in text:
                errs.append(f"{k} 里有禁用词「{w}」：它会让画面变成 AI 精修感，删掉或换成普通说法")
    for k in ("start_pose", "action_1", "action_2"):
        v = b[k]
        if len(v) > 50:
            errs.append(f"{k} 超过 50 个字（现在 {len(v)}）：一段只写一个动作")
        n, rest = 0, v
        for w in SEQ:  # 先数长的（然后再），数过的删掉，避免重复计数
            n += rest.count(w)
            rest = rest.replace(w, "")
        if n > 1:
            errs.append(f"{k} 里「然后/再/接着…」出现 {n} 次：一段最多一个转折，动作太多会每个都做一半")
        if len(re.findall(r"[，,、；;]", v)) > 2:
            errs.append(f"{k} 用逗号/顿号串了太多动作：一段最多 3 个短句，最好只有一个主要动作")
        bad = SELFIE_ONLY if b["camera"] == "朋友跟拍" else FRIEND_ONLY
        for w in bad:
            if w in v:
                why = ("朋友跟拍时人物手里没有手机，改成对镜头旁边的朋友说话、转身展示衣服这类动作" if b["camera"] == "朋友跟拍"
                       else "自拍时一只手一直举着手机，不能用双手、也没有朋友在拍；要这类动作就把 camera 改成 朋友跟拍")
                errs.append(f"{k} 里有「{w}」：{why}")
    return errs


def need_lint(d):
    b = load(d)
    errs = lint_errors(b)
    if errs:
        die("brief.json 没通过检查，先改好再跑：\n" + "\n".join(errs))
    return b


def cmd_init(a):
    d = os.path.join(OUTBASE, f"{datetime.date.today():%Y-%m-%d}-{a.name}")
    if os.path.exists(d):
        die(f"已存在：{d}，换个名称")
    os.makedirs(d)
    shutil.copy(os.path.join(SKILL, "brief.example.json"), os.path.join(d, "brief.json"))
    log(d, "init：新建作品文件夹，brief.json 是示例，要按这次的模特和衣服改")
    print("OK", d)


def cmd_lint(a):
    errs = lint_errors(load(a.dir))
    if errs:
        print("\n".join("❌ " + e for e in errs))
        sys.exit(1)
    print("OK brief.json 检查通过")


def codex_img(d, prompt_name, note_name, out, base=None, ref=None, ratio="9:16"):
    b = load(d)
    os.makedirs(os.path.join(d, "提示词"), exist_ok=True)
    pf = os.path.join(d, "提示词", prompt_name)
    open(pf, "w", encoding="utf-8").write(render(prompt_name, b))
    note = open(os.path.join(SKILL, "templates", note_name), encoding="utf-8").read().strip()
    cmd = [sys.executable, "-I", os.path.join(VP, "codex_img.py"), "--prompt-file", pf, "--out", out,
           "--note", note, "--ratio", ratio]
    if base:
        cmd += ["--base", base]
    if ref:
        cmd += ["--ref", ref]
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0 or not os.path.exists(out):
        die("出图失败（常见原因：ChatGPT 额度用完，按提示的时间之后再试）")


def face_crop(x, y, w, h):
    """按比例裁脸，先把画面统一成 9:16 再裁，左右两张高度统一"""
    return f"scale=768:1344:force_original_aspect_ratio=increase,crop=768:1344,crop={w}:{h}:{x}:{y},scale=-2:500"


def cmd_tryon(a):
    b = need_lint(a.dir)
    out, k = nxt(a.dir, "生成图", "换装", ".png")
    codex_img(a.dir, "tryon.txt", "tryon-note.txt", out, base=ab(b["model_image"]), ref=ab(b["garment_image"]),
              ratio=ratio_of(ab(b["model_image"])))
    rv = os.path.join(a.dir, f"审核-换装-{k}.jpg")
    ff("-i", ab(b["model_image"]), "-i", ab(b["garment_image"]), "-i", out, "-filter_complex",
       "[0]scale=-2:1200[a];[1]scale=1200:1200:force_original_aspect_ratio=decrease,pad=1200:1200:(ow-iw)/2:(oh-ih)/2:white[b];"
       "[2]scale=-2:1200[c];[a][b][c]hstack=3", rv)
    fc = os.path.join(a.dir, f"审核-换装脸-{k}.jpg")
    crop = "scale=-2:1200,crop=iw*0.4:ih*0.26:iw*0.3:ih*0.02,scale=-2:500"
    ff("-i", ab(b["model_image"]), "-i", out, "-filter_complex", f"[0]{crop}[a];[1]{crop}[b];[a][b]hstack", fc)
    log(a.dir, f"tryon：生成图/换装-{k}.png（ChatGPT 额度，不花钱）")
    print("OK", out)
    print(f"下一步：打开 {rv} 和 {fc}，按 SKILL.md 检查点 1 逐条检查（脸部图里如果没拍到脸，就直接看 {rv}）")
    print(f"全部合格就运行：{ME} pick \"{a.dir}\" tryon \"生成图/换装-{k}.png\"")


def cmd_pick(a):
    load(a.dir)
    src = a.file if os.path.isabs(a.file) else os.path.join(a.dir, a.file)
    if not os.path.exists(src):
        src = ab(a.file)
    if not os.path.exists(src):
        die(f"找不到 {a.file}（写作品文件夹里的相对路径，如 生成图/换装-02.png）")
    want = {"tryon": "换装-", "frame": "首帧-"}[a.kind]
    if not os.path.basename(src).startswith(want):
        die(f"{a.kind} 只能定稿 {want}XX.png 这种文件，你给的是 {os.path.basename(src)}")
    fin = os.path.join(a.dir, "定稿")
    os.makedirs(fin, exist_ok=True)
    dst = os.path.join(fin, {"tryon": "换装.png", "frame": "首帧.png"}[a.kind])
    old_frame = os.path.join(fin, "首帧.png")
    if a.kind == "tryon" and os.path.exists(old_frame):  # 换装照换了，旧首帧作废（改名保留，不删除）
        os.replace(old_frame, os.path.join(fin, f"首帧-作废-{datetime.datetime.now():%H%M%S}.png"))
        print("注意：换装照换了，旧的定稿首帧已作废，要重新 frame")
    shutil.copy(src, dst)
    log(a.dir, f"pick：{a.kind} 定稿 ← {os.path.basename(src)}")
    print("OK", dst)


def cmd_frame(a):
    b = need_lint(a.dir)
    ref = os.path.join(a.dir, "定稿", "换装.png")
    if not os.path.exists(ref):
        die("还没有定稿的换装照：先 tryon，检查合格后 pick tryon")
    out, k = nxt(a.dir, "生成图", "首帧", ".png")
    codex_img(a.dir, "frame.txt", "frame-note.txt", out, ref=ref)
    rv = os.path.join(a.dir, f"审核-首帧-{k}.jpg")
    ff("-i", ref, "-i", out, "-filter_complex", "[0]scale=-2:1200[a];[1]scale=-2:1200[b];[a][b]hstack", rv)
    log(a.dir, f"frame（{b['camera']}）：生成图/首帧-{k}.png（ChatGPT 额度，不花钱）")
    print("OK", out)
    print(f"下一步：打开 {rv}，按 SKILL.md 检查点 2 逐条检查")
    print(f"全部合格就运行：{ME} pick \"{a.dir}\" frame \"生成图/首帧-{k}.png\"")


def cmd_video(a):
    b = need_lint(a.dir)
    first = os.path.join(a.dir, "定稿", "首帧.png")
    if not os.path.exists(first):
        die("还没有定稿的首帧：先 frame，检查合格后 pick frame")
    vids = os.path.join(a.dir, "视频片段")
    if not a.resume:
        if not a.spend:
            die("这一步花 MiniMax 的钱（1 次）。确认用户同意后加 --spend 再运行")
        if os.path.isdir(vids) and os.listdir(vids) and not a.again:
            die("这个作品已经生成过视频（视频片段/ 里有文件）。再生成会再花 1 次钱：先问用户，同意后加 --again")
    pf = os.path.join(a.dir, "提示词", "video.txt")
    open(pf, "w", encoding="utf-8").write(render("video.txt", b))
    raw, k = nxt(a.dir, "视频片段", "vlog", ".mp4")
    tf = os.path.join(a.dir, f"task_id-{k}.txt")
    cmd = [sys.executable, "-I", os.path.join(VP, "mm.py"), "video", "--prompt-file", pf, "--first", first,
           "--out", raw, "--duration", "6", "--resolution", "768P", "--ratio", "9:16", "--task-file", tf]
    if a.resume:
        cmd += ["--resume", a.resume]
    r = subprocess.run(cmd, cwd=ROOT)
    tid = open(tf, encoding="utf-8").read().strip() if os.path.exists(tf) else a.resume
    if tid and not a.resume:
        log(a.dir, f"video：**已提交 H3，花费 1 次**（task_id {tid}）")
    if r.returncode != 0 or not os.path.exists(raw):
        if tid:
            log(a.dir, f"video：task_id {tid} 已扣费但没拿到视频，用 --resume {tid} 续取，不要重新 --spend")
            die(f"已扣费但没拿到视频。不要重新 --spend，运行：{ME} video \"{a.dir}\" --resume {tid}")
        log(a.dir, "video：提交失败，没有扣费")
        die("H3 提交失败（没有扣费）：1008 = 余额不足，让用户充值；1027 = 内容审核拦截，见 SKILL.md「常见失败」")
    log(a.dir, f"video：拿到视频 视频片段/vlog-{k}.mp4")
    try:
        clean = os.path.join(a.dir, f"成片-{k}-无颗粒.mp4")
        grain = os.path.join(a.dir, f"成片-{k}.mp4")
        ff("-i", raw, "-vf", "scale=1080:1920:flags=lanczos,format=yuv420p", "-an", "-c:v", "libx264", "-crf", "18", clean)
        # 加手机噪点：抵消 H3 自动磨皮的塑料感（推断有效，需对比验证）。不加锐化：实测会把脸上高光放大成油光
        ff("-i", raw, "-vf", "scale=1080:1920:flags=lanczos,noise=alls=7:allf=t+u,format=yuv420p",
           "-an", "-c:v", "libx264", "-crf", "18", grain)
        sheet = os.path.join(a.dir, f"审核-视频抽帧-{k}.jpg")
        ff("-i", raw, "-vf", "fps=2,scale=240:-2,tile=6x2", "-frames:v", "1", sheet)
        face = os.path.join(a.dir, f"审核-视频脸-{k}.jpg")
        crop = face_crop(115, 134, 422, 430) if b["camera"] == "自拍" else face_crop(230, 40, 310, 330)
        ff("-i", first, "-ss", "2", "-i", raw, "-filter_complex", f"[0]{crop}[a];[1]{crop}[b];[a][b]hstack", "-frames:v", "1", face)
    except subprocess.CalledProcessError as e:
        log(a.dir, f"video：后期处理出错（{e}），原始视频在 视频片段/vlog-{k}.mp4，不要重新生成")
        die(f"视频已经生成好了（{raw}），只是后期处理出错。不要重新 --spend，把这个报错原样告诉用户")
    log(a.dir, f"video：成片 成片-{k}.mp4（加颗粒）/ 成片-{k}-无颗粒.mp4")
    print("OK", grain)
    print(f"下一步：打开 {sheet} 和 {face}，按 SKILL.md 检查点 3 打分（脸部图里如果没拍到脸，就看抽帧图）")


p = argparse.ArgumentParser()
s = p.add_subparsers(dest="cmd", required=True)
x = s.add_parser("init"); x.add_argument("name"); x.set_defaults(f=cmd_init)
for n, f in (("lint", cmd_lint), ("tryon", cmd_tryon), ("frame", cmd_frame)):
    x = s.add_parser(n); x.add_argument("dir"); x.set_defaults(f=f)
x = s.add_parser("pick"); x.add_argument("dir"); x.add_argument("kind", choices=["tryon", "frame"]); x.add_argument("file"); x.set_defaults(f=cmd_pick)
x = s.add_parser("video"); x.add_argument("dir"); x.add_argument("--spend", action="store_true")
x.add_argument("--again", action="store_true"); x.add_argument("--resume"); x.set_defaults(f=cmd_video)
a = p.parse_args()
if hasattr(a, "dir"):
    a.dir = ab(a.dir)
a.f(a)
