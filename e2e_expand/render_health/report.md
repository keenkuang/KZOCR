# W6 渲染健康度回检报告

> 判定：渲染期间 MuPDF 在 fd 2 打印 xref 告警，或页内嵌文本层为空且图像非空白
> → healthy=False。
> 注意：KZOCR 基于**渲染图像**做 OCR，xref 告警/文本层缺失本身不直接丢字；
> 仅当渲染图像本身损坏才会丢字。异常页截图见 `render_health/<书>/p<页>.png`。

| 书 | 扫描页 | 异常页 | 异常比例 | 文档级xref | 诊断 |
|---|---|---|---|---|---|
| mi-678.pdf | 100 | 0 | 0.0% | — | 无异常（文本层完整） |
| sh.pdf | 100 | 100 | 100.0% | — | 高比例文本层缺失（100/100）→ 疑似整本扫描件，对图像 OCR 良性 |
| 名老中医之路（全集）.pdf | 100 | 1 | 1.0% | — | 局部异常：文本层缺失 1 页，需逐页核对截图 |
| 全量中药速查总表.pdf | 39 | 1 | 2.6% | ✓ | xref 告警 1 页，但文本层仍完整 → 良性（图像 OCR 不受影响） |
| 264附子 (1).pdf | 53 | 0 | 0.0% | — | 无异常（文本层完整） |
| 265乌头（川乌头）.pdf | 51 | 0 | 0.0% | — | 无异常（文本层完整） |
| 267半夏 (1).pdf | 40 | 0 | 0.0% | — | 无异常（文本层完整） |
| 268虎掌（天南星）.pdf | 30 | 0 | 0.0% | — | 无异常（文本层完整） |
| 中医中西医《重点解读》中基、中诊、中药、方剂、针灸、四大经典.pdf | 28 | 0 | 0.0% | — | 无异常（文本层完整） |

## 异常页明细

### sh.pdf（100 页）

| 页 | xref告警 | 文本层缺失 | 文本层长度 | 图像std | 墨迹覆盖 | 截图 |
|---|---|---|---|---|---|---|
| 0 | — | ✓ | 0 | 38.8 | 4.3% | render_health/sh.pdf/p0.png |
| 1 | — | ✓ | 0 | 31.0 | 1.5% | render_health/sh.pdf/p1.png |
| 2 | — | ✓ | 0 | 33.8 | 2.4% | render_health/sh.pdf/p2.png |
| 3 | — | ✓ | 0 | 38.0 | 4.1% | render_health/sh.pdf/p3.png |
| 4 | — | ✓ | 0 | 37.7 | 4.0% | render_health/sh.pdf/p4.png |
| 5 | — | ✓ | 0 | 35.6 | 3.3% | render_health/sh.pdf/p5.png |
| 6 | — | ✓ | 0 | 34.4 | 2.7% | render_health/sh.pdf/p6.png |
| 7 | — | ✓ | 0 | 38.5 | 4.5% | render_health/sh.pdf/p7.png |
| 8 | — | ✓ | 0 | 38.6 | 4.5% | render_health/sh.pdf/p8.png |
| 9 | — | ✓ | 0 | 33.0 | 2.2% | render_health/sh.pdf/p9.png |
| 10 | — | ✓ | 0 | 33.4 | 3.2% | render_health/sh.pdf/p10.png |
| 11 | — | ✓ | 0 | 38.2 | 4.4% | render_health/sh.pdf/p11.png |
| 12 | — | ✓ | 0 | 41.4 | 5.8% | render_health/sh.pdf/p12.png |
| 13 | — | ✓ | 0 | 35.0 | 3.8% | render_health/sh.pdf/p13.png |
| 14 | — | ✓ | 0 | 34.9 | 3.5% | render_health/sh.pdf/p14.png |
| 15 | — | ✓ | 0 | 36.5 | 3.6% | render_health/sh.pdf/p15.png |
| 16 | — | ✓ | 0 | 33.8 | 3.2% | render_health/sh.pdf/p16.png |
| 17 | — | ✓ | 0 | 35.5 | 3.5% | render_health/sh.pdf/p17.png |
| 18 | — | ✓ | 0 | 36.5 | 4.2% | render_health/sh.pdf/p18.png |
| 19 | — | ✓ | 0 | 34.9 | 3.2% | render_health/sh.pdf/p19.png |
| 20 | — | ✓ | 0 | 36.4 | 3.6% | render_health/sh.pdf/p20.png |
| 21 | — | ✓ | 0 | 34.6 | 3.3% | render_health/sh.pdf/p21.png |
| 22 | — | ✓ | 0 | 33.0 | 3.0% | render_health/sh.pdf/p22.png |
| 23 | — | ✓ | 0 | 33.8 | 2.6% | render_health/sh.pdf/p23.png |
| 24 | — | ✓ | 0 | 34.2 | 3.1% | render_health/sh.pdf/p24.png |
| 25 | — | ✓ | 0 | 37.3 | 4.2% | render_health/sh.pdf/p25.png |
| 26 | — | ✓ | 0 | 34.4 | 2.8% | render_health/sh.pdf/p26.png |
| 27 | — | ✓ | 0 | 37.5 | 4.4% | render_health/sh.pdf/p27.png |
| 28 | — | ✓ | 0 | 34.1 | 2.9% | render_health/sh.pdf/p28.png |
| 29 | — | ✓ | 0 | 31.5 | 2.5% | render_health/sh.pdf/p29.png |
| 30 | — | ✓ | 0 | 31.7 | 2.0% | render_health/sh.pdf/p30.png |
| 31 | — | ✓ | 0 | 35.0 | 3.1% | render_health/sh.pdf/p31.png |
| 32 | — | ✓ | 0 | 35.7 | 3.6% | render_health/sh.pdf/p32.png |
| 33 | — | ✓ | 0 | 32.8 | 2.6% | render_health/sh.pdf/p33.png |
| 34 | — | ✓ | 0 | 33.2 | 2.8% | render_health/sh.pdf/p34.png |
| 35 | — | ✓ | 0 | 33.8 | 2.9% | render_health/sh.pdf/p35.png |
| 36 | — | ✓ | 0 | 30.2 | 2.0% | render_health/sh.pdf/p36.png |
| 37 | — | ✓ | 0 | 29.7 | 1.6% | render_health/sh.pdf/p37.png |
| 38 | — | ✓ | 0 | 30.5 | 1.8% | render_health/sh.pdf/p38.png |
| 39 | — | ✓ | 0 | 30.8 | 2.1% | render_health/sh.pdf/p39.png |
| 40 | — | ✓ | 0 | 33.6 | 2.9% | render_health/sh.pdf/p40.png |
| 41 | — | ✓ | 0 | 31.1 | 2.1% | render_health/sh.pdf/p41.png |
| 42 | — | ✓ | 0 | 29.7 | 1.6% | render_health/sh.pdf/p42.png |
| 43 | — | ✓ | 0 | 32.7 | 2.7% | render_health/sh.pdf/p43.png |
| 44 | — | ✓ | 0 | 34.2 | 3.5% | render_health/sh.pdf/p44.png |
| 45 | — | ✓ | 0 | 29.4 | 1.7% | render_health/sh.pdf/p45.png |
| 46 | — | ✓ | 0 | 33.5 | 2.9% | render_health/sh.pdf/p46.png |
| 47 | — | ✓ | 0 | 33.9 | 3.0% | render_health/sh.pdf/p47.png |
| 48 | — | ✓ | 0 | 35.8 | 3.9% | render_health/sh.pdf/p48.png |
| 49 | — | ✓ | 0 | 34.4 | 3.3% | render_health/sh.pdf/p49.png |
| 50 | — | ✓ | 0 | 37.3 | 4.4% | render_health/sh.pdf/p50.png |
| 51 | — | ✓ | 0 | 36.2 | 3.6% | render_health/sh.pdf/p51.png |
| 52 | — | ✓ | 0 | 37.2 | 4.2% | render_health/sh.pdf/p52.png |
| 53 | — | ✓ | 0 | 36.9 | 3.7% | render_health/sh.pdf/p53.png |
| 54 | — | ✓ | 0 | 36.6 | 3.8% | render_health/sh.pdf/p54.png |
| 55 | — | ✓ | 0 | 34.0 | 2.8% | render_health/sh.pdf/p55.png |
| 56 | — | ✓ | 0 | 34.1 | 3.1% | render_health/sh.pdf/p56.png |
| 57 | — | ✓ | 0 | 35.8 | 3.4% | render_health/sh.pdf/p57.png |
| 58 | — | ✓ | 0 | 34.3 | 2.9% | render_health/sh.pdf/p58.png |
| 59 | — | ✓ | 0 | 36.4 | 3.7% | render_health/sh.pdf/p59.png |
| 60 | — | ✓ | 0 | 35.9 | 3.8% | render_health/sh.pdf/p60.png |
| 61 | — | ✓ | 0 | 34.1 | 2.8% | render_health/sh.pdf/p61.png |
| 62 | — | ✓ | 0 | 33.3 | 2.4% | render_health/sh.pdf/p62.png |
| 63 | — | ✓ | 0 | 33.5 | 2.6% | render_health/sh.pdf/p63.png |
| 64 | — | ✓ | 0 | 35.4 | 3.1% | render_health/sh.pdf/p64.png |
| 65 | — | ✓ | 0 | 33.3 | 2.6% | render_health/sh.pdf/p65.png |
| 66 | — | ✓ | 0 | 32.3 | 2.5% | render_health/sh.pdf/p66.png |
| 67 | — | ✓ | 0 | 34.8 | 3.1% | render_health/sh.pdf/p67.png |
| 68 | — | ✓ | 0 | 31.2 | 2.0% | render_health/sh.pdf/p68.png |
| 69 | — | ✓ | 0 | 35.1 | 3.1% | render_health/sh.pdf/p69.png |
| 70 | — | ✓ | 0 | 34.3 | 3.1% | render_health/sh.pdf/p70.png |
| 71 | — | ✓ | 0 | 29.7 | 1.9% | render_health/sh.pdf/p71.png |
| 72 | — | ✓ | 0 | 32.7 | 2.7% | render_health/sh.pdf/p72.png |
| 73 | — | ✓ | 0 | 34.4 | 3.1% | render_health/sh.pdf/p73.png |
| 74 | — | ✓ | 0 | 34.0 | 3.3% | render_health/sh.pdf/p74.png |
| 75 | — | ✓ | 0 | 35.8 | 3.5% | render_health/sh.pdf/p75.png |
| 76 | — | ✓ | 0 | 33.6 | 2.7% | render_health/sh.pdf/p76.png |
| 77 | — | ✓ | 0 | 34.3 | 3.4% | render_health/sh.pdf/p77.png |
| 78 | — | ✓ | 0 | 41.2 | 5.8% | render_health/sh.pdf/p78.png |
| 79 | — | ✓ | 0 | 35.8 | 3.4% | render_health/sh.pdf/p79.png |
| 80 | — | ✓ | 0 | 38.8 | 4.9% | render_health/sh.pdf/p80.png |
| 81 | — | ✓ | 0 | 37.7 | 4.0% | render_health/sh.pdf/p81.png |
| 82 | — | ✓ | 0 | 37.7 | 4.7% | render_health/sh.pdf/p82.png |
| 83 | — | ✓ | 0 | 36.1 | 3.7% | render_health/sh.pdf/p83.png |
| 84 | — | ✓ | 0 | 39.2 | 4.7% | render_health/sh.pdf/p84.png |
| 85 | — | ✓ | 0 | 38.0 | 4.3% | render_health/sh.pdf/p85.png |
| 86 | — | ✓ | 0 | 34.9 | 3.1% | render_health/sh.pdf/p86.png |
| 87 | — | ✓ | 0 | 35.6 | 3.8% | render_health/sh.pdf/p87.png |
| 88 | — | ✓ | 0 | 35.8 | 3.9% | render_health/sh.pdf/p88.png |
| 89 | — | ✓ | 0 | 35.9 | 3.7% | render_health/sh.pdf/p89.png |
| 90 | — | ✓ | 0 | 37.0 | 4.2% | render_health/sh.pdf/p90.png |
| 91 | — | ✓ | 0 | 37.0 | 4.3% | render_health/sh.pdf/p91.png |
| 92 | — | ✓ | 0 | 35.4 | 3.7% | render_health/sh.pdf/p92.png |
| 93 | — | ✓ | 0 | 33.1 | 2.6% | render_health/sh.pdf/p93.png |
| 94 | — | ✓ | 0 | 32.8 | 2.5% | render_health/sh.pdf/p94.png |
| 95 | — | ✓ | 0 | 32.5 | 2.5% | render_health/sh.pdf/p95.png |
| 96 | — | ✓ | 0 | 33.7 | 3.1% | render_health/sh.pdf/p96.png |
| 97 | — | ✓ | 0 | 31.6 | 2.1% | render_health/sh.pdf/p97.png |
| 98 | — | ✓ | 0 | 33.6 | 2.4% | render_health/sh.pdf/p98.png |
| 99 | — | ✓ | 0 | 33.3 | 2.5% | render_health/sh.pdf/p99.png |

### 名老中医之路（全集）.pdf（1 页）

| 页 | xref告警 | 文本层缺失 | 文本层长度 | 图像std | 墨迹覆盖 | 截图 |
|---|---|---|---|---|---|---|
| 0 | — | ✓ | 0 | 71.2 | 10.8% | render_health/名老中医之路_全集_.pdf/p0.png |

### 全量中药速查总表.pdf（1 页）

| 页 | xref告警 | 文本层缺失 | 文本层长度 | 图像std | 墨迹覆盖 | 截图 |
|---|---|---|---|---|---|---|
| 34 | ✓ | — | 576 | 46.6 | 5.1% | render_health/全量中药速查总表.pdf/p34.png |

## 总体结论

- 扫描范围：v4 扩面九本，共扫描 **541** 页（每本上限 100 页）。
- 异常页：**102** 页；其中**真丢字风险页（xref 告警且文本层缺失）0 页** —— 即未发现会导致 OCR 丢字的渲染/xref 损坏。
- 异常构成（良性为主）：
  - **整本扫描件（无文本层）**：如 sh.pdf 100/100 页 `text_missing` 但图像 std≈33、墨迹≈3%，属图像化扫描件，对基于渲染图像的 OCR **无影响**。
  - **封面/扉页无文本层**：如《名老中医之路（全集）》p0（封面），局部良性。
  - **xref 告警但文本层仍完整**：如《全量中药速查总表》p34（MuPDF `cannot find object in xref (64 0 R)` 告警，但文本层 576 字完好），告警不直接丢字，良性。
- 结论：**W6 渲染健康度回检未发现系统性丢字风险**，所有异常均对图像 OCR 良性。脚本已改为纯投影降级裁切（`_crop_to_body_fallback`），规避 PaddleX 布局模型（`PP-DocLayoutV3`）偶发 SIGTERM 崩溃，渲染健康度检查无需布局模型。

