from pathlib import Path
from datetime import date, datetime

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "招商证券：黄金图表整理2606.xlsx"
SHEET_NAME = "官方黄金储备"
SITE_DIR = ROOT / "site"
OUTPUT_FILE = SITE_DIR / "index.html"


def format_date(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def read_data():
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("读取 Excel 数据需要 openpyxl，请在当前 Python 环境中安装后重试。") from exc

    if not DATA_FILE.exists():
        raise FileNotFoundError(f"未找到数据文件：{DATA_FILE}")

    workbook = load_workbook(DATA_FILE, read_only=True, data_only=True)
    if SHEET_NAME not in workbook.sheetnames:
        raise ValueError(f"未找到工作表：{SHEET_NAME}")

    sheet = workbook[SHEET_NAME]
    rows = []
    for row in sheet.iter_rows(min_row=4, max_col=3, values_only=True):
        date_value, china_reserves, global_reserves = row
        if date_value is None or china_reserves is None or global_reserves is None:
            continue
        if not isinstance(china_reserves, (int, float)) or not isinstance(global_reserves, (int, float)):
            continue

        rows.append({
            "date": format_date(date_value),
            "china_reserves": float(china_reserves),
            "global_reserves": float(global_reserves),
        })

    workbook.close()

    rows.sort(key=lambda x: x["date"])

    for i, row in enumerate(rows):
        if i == 0:
            row["china_mom_change"] = None
            row["global_mom_change"] = None
        else:
            row["china_mom_change"] = row["china_reserves"] - rows[i - 1]["china_reserves"]
            row["global_mom_change"] = row["global_reserves"] - rows[i - 1]["global_reserves"]

    return rows


def make_bar_chart(rows, change_key, aria_label, bar_class):
    chart_rows = [r for r in rows if r[change_key] is not None][-24:]
    changes = [r[change_key] for r in chart_rows]
    if not changes:
        return ""

    max_abs = max(abs(x) for x in changes) or 1

    bars = []
    x = 40
    width = 34
    gap = 12
    mid_y = 120
    scale = 90 / max_abs

    for r in chart_rows:
        change = r[change_key]
        height = abs(change) * scale
        y = mid_y - height if change >= 0 else mid_y
        label = r["date"][2:7]

        bars.append(f"""
        <g>
          <rect class="{bar_class}" x="{x}" y="{y:.1f}" width="{width}" height="{height:.1f}" rx="3"></rect>
          <text x="{x + width / 2}" y="245" text-anchor="middle" font-size="11">{label}</text>
          <text x="{x + width / 2}" y="{y - 6 if change >= 0 else y + height + 16:.1f}" text-anchor="middle" font-size="11">{change:+.1f}</text>
        </g>
        """)
        x += width + gap

    svg_width = max(720, x + 20)

    return f"""
    <svg viewBox="0 0 {svg_width} 270" role="img" aria-label="{aria_label}">
      <line x1="30" y1="{mid_y}" x2="{svg_width - 20}" y2="{mid_y}" stroke="#999" stroke-width="1"></line>
      {''.join(bars)}
    </svg>
    """


def describe_change(change):
    if change > 0:
        return "增加"
    if change < 0:
        return "减少"
    return "持平"


def make_commentary(latest):
    china_change = latest["china_mom_change"]
    global_change = latest["global_mom_change"]

    if china_change is None or global_change is None:
        return "暂无足够数据计算环比变化。"

    return (
        f"最新数据为 {latest['date']}，中国央行黄金储备为 {latest['china_reserves']:.2f} 吨，"
        f"较上月{describe_change(china_change)} {abs(china_change):.2f} 吨；"
        f"全球央行黄金储备为 {latest['global_reserves']:.2f} 吨，"
        f"较上月{describe_change(global_change)} {abs(global_change):.2f} 吨。"
    )


def build_html(rows):
    latest = rows[-1]
    commentary = make_commentary(latest)
    china_chart = make_bar_chart(rows, "china_mom_change", "中国央行黄金储备环比变化柱状图", "china-bar")
    global_chart = make_bar_chart(rows, "global_mom_change", "全球央行黄金储备环比变化柱状图", "global-bar")

    table_rows = []
    for r in reversed(rows):
        china_change = "" if r["china_mom_change"] is None else f"{r['china_mom_change']:+.2f}"
        global_change = "" if r["global_mom_change"] is None else f"{r['global_mom_change']:+.2f}"
        table_rows.append(f"""
        <tr>
          <td>{r['date']}</td>
          <td>{r['china_reserves']:.2f}</td>
          <td>{r['global_reserves']:.2f}</td>
          <td>{china_change}</td>
          <td>{global_change}</td>
        </tr>
        """)

    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>央行黄金储备跟踪</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 960px;
      margin: 40px auto;
      padding: 0 20px;
      line-height: 1.6;
      color: #222;
    }}
    h1 {{
      font-size: 32px;
      margin-bottom: 8px;
    }}
    .subtitle {{
      color: #666;
      margin-bottom: 28px;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin: 24px 0;
    }}
    .card {{
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 18px;
      background: #fafafa;
    }}
    .card .label {{
      color: #666;
      font-size: 14px;
    }}
    .card .value {{
      font-size: 28px;
      font-weight: 700;
      margin-top: 6px;
    }}
    .commentary {{
      border-left: 4px solid #333;
      padding-left: 16px;
      margin: 28px 0;
      font-size: 18px;
    }}
    svg {{
      width: 100%;
      height: auto;
      margin: 20px 0 32px;
    }}
    .china-bar {{
      fill: #356859;
    }}
    .global-bar {{
      fill: #9c6f2d;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      margin-top: 20px;
    }}
    th, td {{
      border-bottom: 1px solid #ddd;
      padding: 10px;
      text-align: right;
    }}
    th:first-child, td:first-child {{
      text-align: left;
    }}
    .footer {{
      margin-top: 32px;
      color: #777;
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <h1>央行黄金储备跟踪</h1>
  <div class="subtitle">单位：吨；数据来自 data/招商证券：黄金图表整理2606.xlsx 的「官方黄金储备」工作表。</div>

  <div class="cards">
    <div class="card">
      <div class="label">最新日期</div>
      <div class="value">{latest['date']}</div>
    </div>
    <div class="card">
      <div class="label">中国央行黄金储备</div>
      <div class="value">{latest['china_reserves']:.2f}</div>
    </div>
    <div class="card">
      <div class="label">全球央行黄金储备</div>
      <div class="value">{latest['global_reserves']:.2f}</div>
    </div>
    <div class="card">
      <div class="label">中国环比变化</div>
      <div class="value">{latest['china_mom_change']:+.2f}</div>
    </div>
    <div class="card">
      <div class="label">全球环比变化</div>
      <div class="value">{latest['global_mom_change']:+.2f}</div>
    </div>
  </div>

  <div class="commentary">{commentary}</div>

  <h2>中国环比变化（最近24个月）</h2>
  {china_chart}

  <h2>全球环比变化（最近24个月）</h2>
  {global_chart}

  <h2>原始数据</h2>
  <table>
    <thead>
      <tr>
        <th>日期</th>
        <th>中国黄金储备</th>
        <th>全球黄金储备</th>
        <th>中国环比变化</th>
        <th>全球环比变化</th>
      </tr>
    </thead>
    <tbody>
      {''.join(table_rows)}
    </tbody>
  </table>

  <div class="footer">
    Last built: {updated_at}
  </div>
</body>
</html>
"""
    return html


def main():
    SITE_DIR.mkdir(exist_ok=True)
    rows = read_data()

    if len(rows) < 2:
        raise ValueError("至少需要两行数据才能计算环比变化。")

    html = build_html(rows)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
