"""Idempotently keep index.html wired to fresh module-level data.

The dashboard is intentionally a static page, so the safest way to prevent a later
manual edit from reintroducing stale limit-up rendering is to assert/repair the
critical wiring on every data workflow run.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"


def main():
    text = INDEX.read_text(encoding="utf-8")
    original = text

    old_head = '<section class="section"><div class="section-head"><h2>涨停板复盘</h2></div><div class="grid4" id="limitCards">'
    new_head = '<section class="section"><div class="section-head"><h2>涨停板复盘</h2><div class="rangehint" id="limitFreshness"></div></div><div class="grid4" id="limitCards">'
    if old_head in text:
        text = text.replace(old_head, new_head, 1)

    # Replace only the limit-up rendering slice inside renderMarket. The anchors are
    # intentionally stable and validation below refuses a partial/ambiguous patch.
    pattern = re.compile(
        r"const hist=m\.limit_history\|\|\[\],latest=.*?const stocks=m\.stocks\|\|\[\];",
        re.DOTALL,
    )
    replacement = r'''const hist=m.limit_history||[],liveLimit=m.live_limit_snapshot||{},liveOk=liveLimit.status==='ok'&&liveLimit.date&&liveLimit.limit_count!=null&&liveLimit.failed_count!=null,historyLatest=hist.find(x=>x.date===m.data_date)||hist.at(-1)||{},latest=liveOk?liveLimit:historyLatest,datasets=m.datasets||{},pool=liveOk?(liveLimit.leaders||[]):((datasets['limits_'+m.data_date]?.rows)||[]);if($('limitFreshness'))$('limitFreshness').textContent=liveOk?`当日快照 ${liveLimit.date} · ${liveLimit.fetched_at?new Date(liveLimit.fetched_at).toLocaleString('zh-CN',{hour12:false}):'—'}`:`完整交易日 ${historyLatest.date||m.data_date||'—'}`;$('limitCards').innerHTML=[['涨停',latest.limit_count,'只'],['炸板/未封住',latest.failed_count,'只'],['封板率',latest.seal_rate_pct,'%'],['最高连板',liveOk?liveLimit.highest_streak:(pool.length?Math.max(...pool.map(x=>Number(x['连板数'])||Number(x.streak)||0)):null),'板']].map(x=>`<div class="card"><div class="k">${x[0]}</div><div class="v">${x[1]==null?'—':fmt(x[1],x[2]=='%'?2:0)+x[2]}</div></div>`).join('');const sectorCount={};if(liveOk){(liveLimit.industry_distribution||[]).forEach(x=>{sectorCount[x.industry||'未分类']=x.count||0})}else{pool.forEach(x=>{const k=x['所属行业']||x.industry||'未分类';sectorCount[k]=(sectorCount[k]||0)+1})}$('limitDetail').innerHTML=`<div class="note">${m.limit_scope||''}${liveOk?` 当前展示 ${liveLimit.date} 实时快照，抓取时间 ${liveLimit.fetched_at||'—'}。`:` 当前实时快照不可用，展示完整交易日历史 ${historyLatest.date||'—'}。`}</div><table><thead><tr><th>日期</th><th>涨停</th><th>未封住</th><th>封板率</th></tr></thead><tbody>${hist.map(x=>`<tr><td>${x.date}</td><td class="up">${x.limit_count}</td><td>${x.failed_count}</td><td>${fmt(x.seal_rate_pct,2)}%</td></tr>`).join('')}</tbody></table><div class="small" style="margin-top:10px">${liveOk?'当日':'最近完整交易日'}行业分布：${Object.entries(sectorCount).sort((a,b)=>b[1]-a[1]).slice(0,10).map(x=>x[0]+' '+x[1]+'只').join('；')||'未取得'}</div>`;const stocks=m.stocks||[];'''

    if "live_limit_snapshot" not in text:
        text, count = pattern.subn(replacement, text, count=1)
        if count != 1:
            raise SystemExit(f"Could not uniquely patch limit rendering; matches={count}")

    # Update the footer/source description so the UI does not claim the entire limit
    # module is completed-session only.
    text = text.replace(
        '板块资金集中榜使用动态全市场行业资金排名；3/5/10日持续性使用完整交易日历史序列。两种口径分开，避免盘中快照与日线混算。',
        '板块资金集中榜使用动态全市场行业资金排名；3/5/10日持续性使用独立历史窗口；涨停板盘中优先使用当日实时快照。各模块独立显示实际日期和更新时间。'
    )

    required = ["live_limit_snapshot", "limitFreshness", "cache:'no-store'", "data_quality"]
    missing = [x for x in required if x not in text]
    if missing:
        raise SystemExit("Frontend freshness wiring incomplete: " + ", ".join(missing))

    if text != original:
        INDEX.write_text(text, encoding="utf-8")
        print("index.html freshness wiring repaired", flush=True)
    else:
        print("index.html freshness wiring already valid", flush=True)


if __name__ == "__main__":
    main()
