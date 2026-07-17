#!/usr/bin/env bash
# 等待 _measure_user_formula.py 进程结束并输出 _measure_v2.log 最终结果
PID=$(pgrep -f "_measure_user_formula.py" | head -1)
if [ -z "$PID" ]; then
  echo "进程未运行，直接读日志"
  cat /home/keen/KZOCR/_measure_v2.log
  exit 0
fi
echo "等待 PID=$PID ..."
while kill -0 "$PID" 2>/dev/null; do
  sleep 15
done
echo "=== 进程已结束，最终日志 ==="
cat /home/keen/KZOCR/_measure_v2.log
