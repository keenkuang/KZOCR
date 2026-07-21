#!/usr/bin/env python3
"""连续古籍扩样脚本：遍历候选书籍，逐本跑 e2e 双引擎分歧比对。

用法：
  nohup python scripts/run_e2e_batch.py > e2e_expand/batch_run.log 2>&1 &
  
中途终止：kill <pid> 或等用户回来叫停。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# 候选书籍列表（尚未跑够 40 页的古籍 PDF）
CANDIDATES = [
    # —— 16 本核心古籍（之前跑过 20-80 页，加深到 100 页或跑满） ——
    # 验方新编上册已跑 80 页，可加深到 150
    "/home/keen/Documents/OCR0625/验方新编-上册-鲍相璈-1990-人民卫生出版社.pdf",
    # 验方新编下册 699 页，已跑 40 页，加深到 100
    "/home/keen/Documents/OCR0625/验方新编-下册-鲍相璈-1990-人民卫生出版社.pdf",
    # 吴氏本草经 428 页，已跑 80 页，加深到 150
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-06/吴氏本草经_魏·吴普.pdf",
    # 名老中医之路全集 1230 页，已跑 40 页，加深到 100
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-07/名老中医之路（全集）.pdf",
    # 秘方求真-570 992 页，已跑 80 页，加深到 150
    "/home/keen/Documents/秘方求真-570.pdf",
    # 本草问答评注 428 页，已跑 80 页，加深到 150
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-06/本草问答评注_唐容川黄杰熙original.pdf",
    # 太医局 428 页，已跑 80 页，加深到 150
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-06/太医局诸科程文格_宋·何大任.pdf",

    # —— 新书（v4 可能未完成，从头跑 40 页） ——
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-07/外经微言 (岐伯天师传，陈士铎述) .pdf",    # 352 页
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-07/针灸疑难奇症医桉荟萃（张登部）.pdf",       # 326 页
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-07/TSP南极先生医案集.pdf",                    # 311 页
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-07/学姚派汗症心得(1).pdf",                    # 194 页
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-07/姚氏三杰医案选.pdf",                        # 311 页
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-07/李保柱仲景方术.pdf",                        # 311 页
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-07/疼痛妙方绝技精粹_12470346.pdf",              # 311 页
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-07/康治本·康平本伤寒论_汉·张仲景.pdf",          # 311 页
    "/home/keen/文档/xwechat_files/keeniskeen_a42b/msg/file/2026-07/古本康平伤寒论_汉·张仲景.pdf",               # 311 页
]

# 每本古籍处理页数
PAGES_PER_BOOK = 40

# 输出目录与汇总文件
OUT_DIR = "e2e_expand"
SUMMARY_FILE = os.path.join(OUT_DIR, "summary_all.json")

LOG_FILE = os.path.join(OUT_DIR, "batch_run.log")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_script_path() -> str:
    return os.path.join(os.path.dirname(__file__), "e2e_expand_books.py")


def main() -> int:
    log(f"扩样批次启动：{len(CANDIDATES)} 本古籍，每本 {PAGES_PER_BOOK} 页")
    log(f"日志：{LOG_FILE}")

    script = get_script_path()
    os.makedirs(OUT_DIR, exist_ok=True)

    total_start = time.monotonic()
    processed = 0
    failed = 0

    for i, pdf in enumerate(CANDIDATES):
        if not os.path.exists(pdf):
            log(f"[{i+1}/{len(CANDIDATES)}] 文件不存在，跳过：{pdf}")
            continue

        book_name = os.path.basename(pdf)
        log(f"[{i+1}/{len(CANDIDATES)}] 开始处理：{book_name}（{PAGES_PER_BOOK} 页）")

        book_start = time.monotonic()
        try:
            result = subprocess.run(
                [sys.executable, script, "--pdf", pdf, "--pages", str(PAGES_PER_BOOK),
                 "--merge", "--out", SUMMARY_FILE],
                capture_output=True, text=True, timeout=7200,  # 2h per book
            )
            elapsed = time.monotonic() - book_start

            if result.returncode == 0:
                log(f"  ✅ {book_name}: {elapsed:.0f}s — OK")
                # 尝试解析输出中的分歧数
                for line in result.stdout.splitlines():
                    if "divergences" in line or "分歧" in line:
                        log(f"  📊 {line.strip()}")
                processed += 1
            else:
                log(f"  ❌ {book_name}: {elapsed:.0f}s — exit={result.returncode}")
                for line in (result.stderr or "").splitlines()[-5:]:
                    if line.strip():
                        log(f"  ERR {line.strip()}")
                failed += 1

        except subprocess.TimeoutExpired:
            log(f"  ⏰ {book_name}: 超时（>2h），跳过")
            failed += 1
        except Exception as e:
            log(f"  💥 {book_name}: {e}")
            failed += 1

        # 书间短暂暂停，避免资源争抢
        time.sleep(5)

    total_elapsed = time.monotonic() - total_start
    log(f"=== 批次完成 ===")
    log(f"处理：{processed} 本成功 / {failed} 本失败 / {len(CANDIDATES)} 本候选")
    log(f"总耗时：{total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
    log(f"汇总：{SUMMARY_FILE}")

    # 生成可读报告
    try:
        import json
        summary = json.load(open(SUMMARY_FILE, encoding="utf-8"))
        log(f"\n--- 分歧汇总 ---")
        total_p = total_d = 0
        for b in sorted(summary, key=lambda x: x.get('divergences',0)/max(x.get('pages',0),1), reverse=True):
            p = b.get('pages',0) or 0
            d = b.get('divergences',0) or 0
            dp = round(d/max(p,1), 1) if p > 0 else 0
            fn = (b.get('book','?').split("/")[-1])[:35]
            log(f"  {fn:<35} {p:>3}p {d:>5}div {dp:>5}/页")
            total_p += p; total_d += d
        log(f"  {'─'*55}")
        log(f"  合计{'':>33} {total_p:>3}p {total_d:>5}div {round(total_d/max(total_p,1),1):>5}/页")
    except Exception:
        log("（合并汇总失败，可稍后手动查看）")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
