"""R5' 情绪层：短线情绪温度计（六阶段）与冰点/黄金坑开关

证据口径（v4 手册 §1.2 情绪周期六阶段 + 8/26 新视频 + USAGE 冰点定义）:
  温度计四指标（8/26 视频每日盘前必看）: 短线情绪℃ / 炸板率 35% / 连板率 10% / 中位数 1.18%
  冰点: 涨停<40 且 跌停>涨停（v47: 涨停34/跌停45; USAGE §1.2）
  高度: ≥5 连板=高度打开（v63/v69）
  涨停家数分档: ≥100 强 / 60~99 中 / <60 弱（手册 §6.3）
  黄金坑: 大盘三次破 4000 点都是黄金坑（A_情绪周期与市场环境.md）

组合打分（规则式，非拟合）:
  temp = 0.40×zt_score + 0.35×quality(炸板率) + 0.25×height
  ⚠️ 权重 0.40/0.35/0.25 无视频出处（8/26 视频仅复合读数 73℃+组分值，R4R5 评审）——
  温度计仅作报告/研究口径，不放行驱动 position_cap；冰点特例(dt>zt 且 zt<40)为避险口径
  冰点特例: dt>zt 直接判冰点（temp≤20）
  六阶段: 冰点≤20 / 修复20-35 / 发酵35-50 / 高潮≥60 / 分歧(高炸板) / 退潮(高炸板+zt回落)
"""
from __future__ import annotations


def emotion_thermometer(zt: int, dt: int | None, zhaban_rate: float, max_h: int,
                       lianban_rate: float | None = None,
                       median_pct: float | None = None,
                       deep_drop_count: int | None = None) -> dict:
    """短线情绪温度计。返回 {temp(0-100), stage, dims, note}

    zt: 涨停家数; dt: 跌停家数(None=未知); zhaban_rate: 炸板率%(炸板/触板);
    max_h: 最高连板; lianban_rate: 昨日涨停今日晋级率%; median_pct: 全市场涨幅中位数%;
    deep_drop_count: **跌幅>7% 家数**（R2.5 2026-09-12 新增；GEN-GATE-17 恐慌度量的跌侧）

    ⚠️ `deep_drop_count` **不参与 temp 加权** —— 人格 SOP 只给了「跌停家数 + 跌幅>7% 家数」
       这两个度量的**存在**，没给权重与阈值；擅自加权等于引入未标定参数（违反「参数只从证据出」）。
       故只进 dims / note 作信息展示，待 R1.3 容差带标定后再考虑纳入。
    """
    dims = {}
    if dt is not None and zt < 40 and dt > zt:
        extra = f'; 跌幅>7% {deep_drop_count} 家' if deep_drop_count is not None else ''
        return {'temp': 15, 'stage': '冰点', 'dims': dims,
                'note': f'冰点特例: zt={zt}<40 且 dt={dt}>zt (v47/USAGE §1.2){extra}'}
    zt_score = max(0.0, min(100.0, zt / 120.0 * 100))
    quality = max(0.0, min(100.0, (1 - zhaban_rate / 50.0) * 100))
    height = max(0.0, min(100.0, max_h / 5.0 * 100))
    temp = 0.40 * zt_score + 0.35 * quality + 0.25 * height
    dims = {'zt_score': round(zt_score, 1), 'quality': round(quality, 1),
            'height': round(height, 1)}
    # 阶段分带（六阶段无手册数值阈值；按 2026 实现分布分位校准，非参数拟合）:
    # 冰点<30(含冰点特例) / 修复30-45 / 发酵45-60 / 高潮≥60
    stage = '修复'
    if temp >= 60:
        stage = '高潮'
    elif temp >= 45:
        stage = '发酵'
    elif temp >= 30:
        stage = '修复'
    else:
        stage = '冰点'
    note = ''
    if zhaban_rate >= 45:
        note = f'分歧/退潮风险: 炸板率{zhaban_rate:.1f}%≥45%'
    if max_h >= 5:
        note += ('；' if note else '') + f'高度打开 {max_h}板'
    if lianban_rate is not None:
        note += ('；' if note else '') + f'连板率{lianban_rate:.1f}%'
    if median_pct is not None:
        note += ('；' if note else '') + f'中位数{median_pct:+.2f}%'
    # R2.5 跌侧展示（GEN-GATE-17：恐慌度量 = 跌停家数 + 跌幅>7% 家数）。不参与 temp 加权。
    if deep_drop_count is not None:
        note += ('；' if note else '') + f'跌幅>7% {deep_drop_count} 家'
        dims['deep_drop'] = int(deep_drop_count)
    return {'temp': round(temp, 1), 'stage': stage, 'dims': dims, 'note': note}


def gold_pit_zone(index_close: float | None, pit_level: float = 4000.0) -> dict:
    """黄金坑开关（A_情绪周期: 大盘三次破 4000 点都是黄金坑）
    返回 {zone, note}: 破 pit_level = 机会区(黄金坑), 上方 = 常态区"""
    if index_close is None:
        return {'zone': 'unknown', 'note': '指数数据缺失'}
    if index_close < pit_level:
        return {'zone': 'gold_pit',
                'note': f'指数{index_close:.0f} < {pit_level:.0f} = 黄金坑机会区(历史三次破位均反弹)'}
    return {'zone': 'normal', 'note': f'指数{index_close:.0f} ≥ {pit_level:.0f} 常态区'}


if __name__ == '__main__':
    # 锚日抽验（选手仓位锚: 4/21 99.8% / 6/18 69.3% / 7/6 34.8% / 7/17 20% / 8/13 51.2%）
    for d, zt, dt, zr, mh in [('2026-04-21', 50, None, 23.1, 4),
                              ('2026-06-18', 94, None, 37.3, 4),
                              ('2026-07-06', 79, None, 44.0, 4),
                              ('2026-07-17', 35, None, 44.4, 3),
                              ('2026-08-13', 64, None, 44.3, 5)]:
        t = emotion_thermometer(zt, dt, zr, mh)
        print(d, 'temp=', t['temp'], t['stage'], t['dims'], t['note'])