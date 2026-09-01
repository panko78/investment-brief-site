from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"


def main():
    s = INDEX.read_text(encoding="utf-8")

    s = s.replace(
        "let charts={};let marginSeries=[];let turnoverSeries=[];let liquidityRange=10;",
        "let charts={};let marginSeries=[];let turnoverSeries=[];let shIndexSeries=[];let liquidityRange=10;",
        1,
    )

    if 'id="shIndexSource"' not in s:
        s = s.replace(
            '<div class="source" id="turnoverSource"></div>\n</div>',
            '<div class="source" id="turnoverSource"></div>\n  <div class="source" id="shIndexSource"></div>\n</div>',
            1,
        )

    if "shIndexSeries=(liq.sh_index_series||[])" not in s:
        s = s.replace(
            "turnoverSeries=(liq.turnover_series||fallback.map(x=>({date:x.date,turnover:x.turnover}))).filter(x=>x.turnover!=null).slice(-30);",
            "turnoverSeries=(liq.turnover_series||fallback.map(x=>({date:x.date,turnover:x.turnover}))).filter(x=>x.turnover!=null).slice(-30);\n  shIndexSeries=(liq.sh_index_series||[]).filter(x=>x.sh_close!=null).slice(-30);",
            1,
        )

    if "getElementById('shIndexSource')" not in s:
        s = s.replace(
            "document.getElementById('turnoverSource').textContent=(liq.turnover_source||liq.source||'')+(liq.status?' · '+liq.status:'');",
            "document.getElementById('turnoverSource').textContent=(liq.turnover_source||liq.source||'')+(liq.status?' · '+liq.status:'');\n  document.getElementById('shIndexSource').textContent=liq.sh_index_source||'上证指数（000001）日收盘点位';",
            1,
        )

    start = s.index("function drawLiquidityCharts(){")
    end = s.index("\nfunction renderTrade", start)
    new_js = r'''function drawLiquidityCharts(){
  const ms=marginSeries.slice(-liquidityRange),ts=turnoverSeries.slice(-liquidityRange),ss=shIndexSeries.slice(-liquidityRange);
  const mm=new Map(ms.map(x=>[x.date,x.margin_balance]));
  const tm=new Map(ts.map(x=>[x.date,x.turnover]));
  const sm=new Map(ss.map(x=>[x.date,x.sh_close]));
  const dates=[...new Set([...ms.map(x=>x.date),...ts.map(x=>x.date),...ss.map(x=>x.date)])].sort().slice(-liquidityRange);
  destroy('margin');destroy('turnover');destroy('liquidity');
  charts.liquidity=new Chart(document.getElementById('liquidityChart'),{
    type:'line',
    data:{
      labels:dates.map(x=>x.slice(5)),
      datasets:[
        {label:'全A两融余额（亿元）',data:dates.map(d=>mm.has(d)?mm.get(d):null),yAxisID:'yAmount',tension:.2,pointRadius:3,spanGaps:false,borderColor:'#2563eb',backgroundColor:'#2563eb'},
        {label:'沪深两市成交额（亿元）',data:dates.map(d=>tm.has(d)?tm.get(d):null),yAxisID:'yAmount',tension:.2,pointRadius:3,spanGaps:false,borderColor:'#f59e0b',backgroundColor:'#f59e0b'},
        {label:'上证指数（点）',data:dates.map(d=>sm.has(d)?sm.get(d):null),yAxisID:'yIndex',tension:.2,pointRadius:3,spanGaps:false,borderColor:'#dc2626',backgroundColor:'#dc2626'}
      ]
    },
    options:{
      responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
      plugins:{title:{display:true,text:`两融余额 / 沪深成交额 / 上证指数 · 近${liquidityRange}个交易日`}},
      scales:{
        yAmount:{type:'linear',position:'left',title:{display:true,text:'两融余额 / 成交额（亿元）'},grid:{drawOnChartArea:true}},
        yIndex:{type:'linear',position:'right',title:{display:true,text:'上证指数（点）'},grid:{drawOnChartArea:false}}
      }
    }
  });
}'''
    s = s[:start] + new_js + s[end:]

    s = s.replace(
        "沪深成交额按上交所与深交所市场成交汇总口径；进出口、GDP、社融、债务与外汇储备使用中国官方数据；",
        "沪深成交额按上交所与深交所市场成交汇总口径；上证指数为000001日收盘点位；进出口、GDP、社融、债务与外汇储备使用中国官方数据；",
        1,
    )

    INDEX.write_text(s, encoding="utf-8")


if __name__ == "__main__":
    main()
