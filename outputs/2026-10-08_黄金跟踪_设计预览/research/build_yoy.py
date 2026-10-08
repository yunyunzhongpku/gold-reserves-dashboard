"""算术同比的描述性补充；只读取已保存输入，不刷新数据。"""

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
from build_research import corr, monthly_prices, partial_corr, read_rows, shift_month


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEFAULT_ASOF = date(2026, 10, 8)
MONEY_FILE = HERE / "liquidity_monthly.csv"
MARKET_FILE = ROOT / "data/market/wind_daily.csv"


def arithmetic_yoy(values):
    """只比较相隔 12 个月的有效正值，不跨缺失月份填充。"""
    changes = {}
    for month, value in values.items():
        previous = values.get(shift_month(month, -12))
        if value is not None and previous is not None and value > 0 and previous > 0:
            changes[month] = value / previous - 1
    return changes


def describe(points):
    return {
        "n": len(points),
        "start": points[0][0] if points else None,
        "end": points[-1][0] if points else None,
        "correlation": corr([p[1] for p in points], [p[2] for p in points]),
        "partial_dollar": partial_corr(points),
    }


def input_hashes():
    files = (Path(__file__).resolve(), HERE / "build_research.py", MONEY_FILE, MARKET_FILE)
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }


def make_study(asof=DEFAULT_ASOF):
    hashes = input_hashes()
    money_rows = read_rows(MONEY_FILE, asof)
    market_rows = read_rows(MARKET_FILE, asof)
    gold = monthly_prices(market_rows, "gold_price", asof)
    dollar = monthly_prices(market_rows, "dollar_index", asof)
    changes = {
        "gold": arithmetic_yoy({month: row["value"] for month, row in gold.items()}),
        "dollar": arithmetic_yoy({month: row["value"] for month, row in dollar.items()}),
        "usd": arithmetic_yoy({r["date"][:7]: r.get("total_usd_bn") for r in money_rows}),
        "fixed": arithmetic_yoy({r["date"][:7]: r.get("total_fixed_usd_bn") for r in money_rows}),
    }
    common = sorted(set.intersection(*(set(values) for values in changes.values())))

    def result(months):
        return {
            "n": len(months),
            "start": months[0] if months else None,
            "end": months[-1] if months else None,
            "targetMonths": months,
            **{
                kind: describe([
                    (month, changes[kind][month], changes["gold"][month], changes["dollar"][month])
                    for month in months
                ])
                for kind in ("usd", "fixed")
            },
        }

    output = {
        "asOf": asof.isoformat(),
        "inputHashes": hashes,
        "method": {
            "scope": "美国、中国、欧元区 M2 的研究组合，不等于全球总量；与美元计价黄金比较。",
            "change": "X_t / X_(t-12) - 1；黄金、两种 M2 篮子和美元指数均使用算术同比。",
            "units": "fraction；显示百分比时乘 100。相关系数本身无单位。",
            "fx": "usd 为当期汇率折算；fixed 固定 2020-12 汇率。两个口径使用相同目标月份。",
            "control": "控制同期美元指数算术同比；分别对 X/Y 关于截距和美元同比回归后，计算残差的 Pearson 相关。",
            "overlapping": "逐月计算 12 个月同比，相邻观测共享 11 个月变化；月份数不代表独立观察数。",
            "annual": "预先固定每年 12 月的同比，即上年 12 月至本年 12 月；年度区间互不重叠，但不保证统计独立。",
            "prices": "只用已完成月份的自身最后有效价格，距月末不超过 7 天；无插值或跨月填充。美国 M2 月均，中欧 M2 月末，按月近似对齐。",
            "sample": "黄金、美元指数、当期与固定汇率 M2 同比的有效月份交集；年度结果仅保留交集中的 12 月。",
            "interval": "本补充不新增置信区间，不将重叠同比的样本数当作独立样本数。",
            "boundary": "与原研究的非重叠月度对数变化分开；当前修订数据未按历史发布日期回放，相关不是因果、预测或交易信号。",
        },
        "overlapping": result(common),
        "annual": result([month for month in common if month.endswith("-12")]),
    }
    if input_hashes() != hashes:
        raise RuntimeError("读取过程中输入或方法文件发生变化；未生成同比研究，请重试。")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asof", type=date.fromisoformat, default=DEFAULT_ASOF)
    args = parser.parse_args()
    study = make_study(args.asof)
    (HERE / "yoy_study.json").write_text(
        json.dumps(study, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: {k: v for k, v in study[key].items() if k != "targetMonths"}
                      for key in ("overlapping", "annual")}, ensure_ascii=False))
