#!/usr/bin/env python3
"""e2e 跨引擎扩面 · 通宵自主驱动。

行为：
  1. 递归扫描书源根目录（微信文件 + Documents）收集所有 *.pdf；
  2. 用中医/古籍关键词**正向白名单**过滤，剔除财经/AI/旅游/作业等非古籍 PDF；
  3. 排除已扩面书目（历史 summary_merged.json + 今夜 summary_tonight.json，按文件名去重）；
  4. 把未处理书目交给 e2e_expand_books.py（--merge 检查点、可续跑）顺序处理；
  5. 每轮结束后把今夜结果并入 summary_merged.json 并重算 SUMMARY_MERGED.md；
  6. 若无可处理新书则休眠后重扫，等待用户掉落新 PDF（"一直跑"语义）。

单进程顺序执行：避免双 OCR 模型并发 OOM；--merge 保证中途崩溃不丢进度。
用法：
  python scripts/run_e2e_nightly.py            # 默认 40 页/本，循环到无新书
  python scripts/run_e2e_nightly.py --pages 80 --idle 600
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXPAND_DIR = REPO / "e2e_expand"
HIST_JSON = EXPAND_DIR / "summary_merged.json"
TONIGHT_JSON = EXPAND_DIR / "summary_tonight.json"
MERGED_MD = EXPAND_DIR / "SUMMARY_MERGED.md"
NIGHTLY_LOG = EXPAND_DIR / "nightly.log"

ROOTS = [
    "/home/keen/Documents/OCR0625",
    "/home/keen/Documents",
    "/home/keen/文档/xwechat_files",
    "/media/keen/data/4Share/传递",
]

# 中医/古籍正向白名单（文件名命中任一即视为可扩面古籍）
MEDICAL_KW = [
    "医", "药", "方", "伤寒", "本草", "灸", "针", "诊", "疾", "症", "证",
    "汤", "丸", "散", "论", "录", "桉", "案", "秘方", "验方", "素问", "灵枢",
    "金匮", "温病", "瘟", "毒", "脉", "脏", "腑", "阴", "阳", "神农", "黄帝",
    "张仲景", "唐容川", "鲍相璈", "吴普", "陈士铎", "紫极", "姚", "桐君", "运气",
    "康平", "康治", "代赭", "白垩", "冬灰", "青琅", "天雄", "鸢尾", "虎掌", "半夏",
    "附子", "乌头", "赭", "琅", "中基", "中诊", "四大经典", "西学中", "脾", "胃",
    "肝", "肾", "肺", "心", "气血", "经方", "时方", "舌", "饮片", "煎煮", "剂",
    "瘥", "病", "治", "临床", "手册", "培训", "考试", "必背", "口诀", "方歌",
    "穴", "艾", "罐", "推拿", "正骨", "养生", "经络", "偏方", "良方", "集验",
    "普济", "圣惠", "太平惠民", "御药", "本草纲目", "中药", "中医药", "中医生",
]

# 明确非书（测试/临时/未命名）即便误命中也跳过
DENY_SUBSTR = ["test_", "tmp_", "未命名", "main_text", "config_man"]
# 否定关键词：命中任一即视为非古籍（财经/时政/AI/芯片等误判防护）
DENY_KW = ["高盛", "白皮书", "AI", "DeepSeek", "芯片", "ETF", "牛市", "IPO",
           "股票", "回购", "战争", "中东", "白宫", "世界杯", "备份", "财报",
           "智能手机", "床垫", "攻略", "埃拉万", "Erawan", "Condo", "特斯拉",
           "电动汽车", "代币", "品牌资产", "智工", "超级智工"]


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(NIGHTLY_LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def scan_pdfs() -> dict[str, str]:
    """递归扫描根目录，返回 {真实路径: 文件名}，去重。"""
    found: dict[str, str] = {}
    for root in ROOTS:
        r = Path(root)
        if not r.is_dir():
            log(f"[warn] 根目录不存在: {root}")
            continue
        for path in r.rglob("*.pdf"):
            try:
                real = str(path.resolve())
            except Exception:
                continue
            if real in found:
                continue
            found[real] = path.name
    return found


def _book_key(name: str) -> str:
    """文件名归一用于跨路径去重：仅去扩展名与末尾 `(1)`/空格噪声，
    保留版本/册数差异（秘方求真全文 vs 裁切版-上 视为不同书，均有扩面价值）。"""
    import re
    k = name[:-4] if name.lower().endswith(".pdf") else name
    k = re.sub(r"[ ]*\(1\)$", "", k)   # 去末尾 (1)
    return k.strip(" -_")


def is_medical(name: str) -> bool:
    if any(d in name for d in DENY_SUBSTR):
        return False
    if any(d in name for d in DENY_KW):
        return False
    return any(k in name for k in MEDICAL_KW)


def loaded_book_keys(path: Path) -> set[str]:
    """已扩面书目的'书名'集合（用于跨路径/版本去重）。"""
    if not path.is_file():
        return set()
    try:
        with open(path, encoding="utf-8") as fh:
            return {_book_key(r["book"]) for r in json.load(fh)}
    except Exception as exc:
        log(f"[warn] 读取 {path} 失败: {exc}")
        return set()


def build_candidates() -> list[str]:
    done_keys = loaded_book_keys(HIST_JSON) | loaded_book_keys(TONIGHT_JSON)
    found = scan_pdfs()
    inc, skip = [], []
    for real, name in found.items():
        if _book_key(name) in done_keys:
            continue
        (inc if is_medical(name) else skip).append(real)
    log(f"扫描到 PDF {len(found)} 个；已扩面书名 {len(done_keys)}；医学命中 {len(inc)}；跳过 {len(skip)}")
    for p in sorted(skip):
        log(f"  SKIP {p}")
    return sorted(inc)


def run_batch(pdfs: list[str], pages: int) -> None:
    list_file = EXPAND_DIR / "_tonight_list.txt"
    with open(list_file, "w", encoding="utf-8") as fh:
        for p in pdfs:
            fh.write(f"{p} {pages}\n")
    cmd = [
        sys.executable, str(REPO / "scripts" / "e2e_expand_books.py"),
        "--list", str(list_file), "--merge",
        "--out", str(TONIGHT_JSON), "--pages", str(pages), "--dpi", "150",
    ]
    log(f"[run] {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(REPO), check=False)


def merge_and_report() -> None:
    """把今夜结果并入历史并重写为 SUMMARY_MERGED.md。"""
    hist = json.load(open(HIST_JSON, encoding="utf-8")) if HIST_JSON.is_file() else []
    ton = json.load(open(TONIGHT_JSON, encoding="utf-8")) if TONIGHT_JSON.is_file() else []
    by_base: dict[str, dict] = {}
    for r in hist + ton:
        b = r["pdf"]
        if b not in by_base or r.get("pages_processed", 0) > by_base[b].get("pages_processed", 0):
            by_base[b] = r
    merged = list(by_base.values())
    json.dump(merged, open(HIST_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    total_pages = sum(r.get("pages_processed", 0) for r in merged)
    total_div = sum(r.get("total_divergences", 0) for r in merged)
    rows = sorted(merged, key=lambda r: (r.get("total_divergences", 0) /
                     max(r.get("pages_processed", 1), 1)), reverse=True)
    md = ["# e2e 跨引擎扩面 · 合并汇总报告", "",
          f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')}。合并口径：**按书名去重，每本取最深页数的版本**。", ""]
    md += ["## 总览", "",
           f"- 唯一书籍数：**{len(merged)}**",
           f"- 总页数：**{total_pages}**",
           f"- 总分歧数：**{total_div}**"]
    if total_pages:
        md.append(f"- 均值分歧/页：**{total_div/total_pages:.1f}**")
    md.append("")
    md += ["## 逐书明细（按分歧/页降序）", "",
           "| # | 古籍 | 页 | 总分歧 | 分歧/页 |", "|---|------|----|--------|---------|"]
    for i, r in enumerate(rows, 1):
        pp = r.get("pages_processed", 0)
        d = r.get("total_divergences", 0)
        rpp = d / pp if pp else 0
        md.append(f"| {i} | {r['book']} | {pp} | {d} | {rpp:.1f} |")
    md.append("")
    open(MERGED_MD, "w", encoding="utf-8").write("\n".join(md))
    log(f"[merge] 合并完成：唯一 {len(merged)} 本 / {total_pages} 页 / {total_div} 分歧 -> {MERGED_MD.name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=40)
    ap.add_argument("--idle", type=int, default=300, help="无新书时休眠秒数")
    ap.add_argument("--max-rounds", type=int, default=0, help="最大轮次（0=无限，直到无新书后持续休眠重扫）")
    args = ap.parse_args()

    log(f"=== 通宵扩面驱动启动 pages={args.pages} idle={args.idle}s ===")
    round_n = 0
    while True:
        round_n += 1
        if args.max_rounds and round_n > args.max_rounds:
            log("[stop] 已达最大轮次，退出")
            break
        candidates = build_candidates()
        if not candidates:
            log(f"[idle] 本轮无新书，休眠 {args.idle}s 后重扫（等你掉落新 PDF）")
            time.sleep(args.idle)
            continue
        log(f"[round {round_n}] 处理 {len(candidates)} 本未扩面古籍")
        run_batch(candidates, args.pages)
        merge_and_report()
        log(f"[round {round_n}] 完成，继续循环")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
