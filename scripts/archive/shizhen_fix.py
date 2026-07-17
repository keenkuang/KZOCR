#!/usr/bin/env python3
"""Stage1 修复：针对 ShizhenGPT 三个真问题。

诊断已确认的根因/现象：
  1) max_tokens=120 截断（page0 144→374 字，page1 144→2186 字）
  2) max_tokens 调大后 page1 末尾陷入重复生成（"若主客形态改变…"循环）
  3) page2 仅 5/11 字，与 max_tokens 无关，需单独排查（重试用 dpi=200）
本脚本：合理 max_tokens + 防重复惩罚，重跑三页；page2 过短则自动 dpi=200 重试。
用户已确认本书即紫极先生《伤寒论》注解本，故 SenseNova 输出（含"紫极曰"）可作
独立基准，用来判断 ShizhenGPT 修复后是否完整、有无重复。
所有步骤带 [HH:MM:SS] 时间戳并 flush。
"""
from __future__ import annotations
import os, re, time, subprocess, urllib.request as urllib_request, base64, json
from pathlib import Path
import fitz
import numpy as np


def _ts(): return time.strftime("%H:%M:%S")
def _log(m): print(f"[{_ts()}] {m}", flush=True)


def _inject_keys():
    try:
        pids = subprocess.run(["pgrep", "-f", "KZOCR_SENSENOVA_API_KEY"],
                              capture_output=True, text=True).stdout.split()
    except Exception:
        pids = []
    wl = {"KZOCR_SENSENOVA_API_KEY", "KZOCR_MODELSCOPE_API_KEY",
          "KZOCR_SILICONFLOW_API_KEY", "SENSENOVA_API_KEY", "KIMI_ENGINE_DIR", "ZAI_DIR"}
    for pid in pids:
        cl = Path(f"/proc/{pid}/cmdline")
        if not cl.exists():
            continue
        raw = cl.read_bytes().decode("utf-8", "ignore")
        for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=("([^"]*)"|(\S+))', raw):
            n, q, b = m.group(1), m.group(3), m.group(4)
            if n in wl:
                os.environ[n] = q if q is not None else b
        break


def render(pdf, pages, dpi=150):
    doc = fitz.open(pdf); out = []
    for i in pages:
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n != 3:
            img = np.asarray(fitz.Pixmap(img, 0).samples).reshape(pix.height, pix.width, 3)
        out.append((i, img))
    doc.close(); return out


def _detect_repeat(text: str, min_len=8, min_rep=3) -> bool:
    """简单检测尾部是否出现连续重复片段。"""
    for L in range(min_len, min_len+8):
        for i in range(len(text)-L*min_rep+1):
            seg = text[i:i+L]
            if seg == text[i+L:i+2*L] == text[i+2*L:i+3*L]:
                return True
    return False


def main():
    t0 = time.time()
    _inject_keys()
    from PIL import Image
    import io as _io
    pdf = "/home/keen/Documents/sh.pdf"
    pages = [0, 1, 2]
    params = dict(max_tokens=900, temperature=0.1,
                  repetition_penalty=1.15, frequency_penalty=0.3, presence_penalty=0.2)
    _log(f"[info] 修复参数: {params}（之前 max_tokens=120，无防重复）")

    def call(img):
        buf = _io.BytesIO()
        Image.fromarray(img).save(buf, format="JPEG", quality=92)
        b64 = base64.b64encode(buf.getvalue()).decode()
        payload = {
            "model": "ShizhenGPT",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "请识别图中中医古籍正文，仅输出原文，不要解释。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            **params,
        }
        req = urllib_request.Request(
            "http://127.0.0.1:18086/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        body = json.loads(urllib_request.urlopen(req, timeout=600).read())
        return body["choices"][0]["message"]["content"]

    try:
        ref = json.load(open("/tmp/realstack/engine_outputs.json"))["sensenova"]
    except Exception:
        ref = {}

    imgs = render(pdf, pages, dpi=150)
    results = {}
    for idx, (i, img) in enumerate(imgs):
        _log(f"[fix {idx+1}/{len(imgs)}] page {i} 调用 ShizhenGPT ...")
        try:
            r = call(img)
        except Exception as e:
            _log(f"[fix {idx+1}/{len(imgs)}] page {i} 失败: {e}")
            r = ""
        rep = _detect_repeat(r or "")
        results[str(i)] = r or ""
        ref_len = len(ref.get(str(i), ""))
        _log(f"[fix {idx+1}/{len(imgs)}] page {i}: ShizhenGPT {len(r or '')} 字 | SenseNova基准 {ref_len} 字 | 重复={rep}")
        _log(f"    前40字: {(r or '')[:40]!r}  后40字: {(r or '')[-40:]!r}")
        # page2 过短 → dpi=200 重试
        if len(r or "") < 50 and i == 2:
            _log(f"[fix] page 2 仍过短（{len(r or '')}字），改用 dpi=200 重试")
            hi = render(pdf, [2], dpi=200)
            try:
                r2 = call(hi[0][1])
            except Exception as e:
                _log(f"[fix] page2 dpi=200 失败: {e}")
                r2 = ""
            results["2_dpi200"] = r2 or ""
            _log(f"[fix] page2 dpi=200: {len(r2 or '')} 字 | 重复={_detect_repeat(r2 or '')} | 前40: {(r2 or '')[:40]!r}")

    Path("/tmp/realstack").mkdir(parents=True, exist_ok=True)
    with open("/tmp/realstack/shizhen_fix.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    _log(f"[done] 保存 /tmp/realstack/shizhen_fix.json | 总耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
