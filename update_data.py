import json, math, time, re
from pathlib import Path
from datetime import datetime, timedelta
import requests
import pandas as pd
import akshare as ak

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"dashboard.json"
UA={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

def load_data():
    return json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}

def safe_float(x):
    try:
        if pd.isna(x): return None
        return float(str(x).replace(",","").strip())
    except: return None

def last_weekdays(n=22):
    d=datetime.now().date()
    out=[]
    while len(out)<n:
        if d.weekday()<5: out.append(d)
        d-=timedelta(days=1)
    return sorted(out)

def fetch_liquidity(data):
    days=last_weekdays(24)
    start=days[0].strftime("%Y%m%d"); end=days[-1].strftime("%Y%m%d")
    out=[]
    try:
        sh=ak.stock_margin_sse(start_date=start,end_date=end)
        sh_map={}
        for _,r in sh.iterrows():
            ds=str(r["信用交易日期"]).replace("-","")[:8]
            sh_map[ds]=safe_float(r["融资融券余额"])
        for d in days:
            ds=d.strftime("%Y%m%d")
            if ds not in sh_map: continue
            try:
                sz=ak.stock_margin_szse(date=ds)
                if sz is None or sz.empty: continue
                sz_bal=safe_float(sz.iloc[0]["融资融券余额"]) # 亿元
                sh_bal=sh_map[ds]/1e8
                sse=ak.stock_sse_deal_daily(date=ds)
                rr=sse[sse["单日情况"].astype(str).str.contains("成交金额")].iloc[0]
                sh_amt=(safe_float(rr.get("主板A")) or 0)+(safe_float(rr.get("科创板")) or 0) # 亿元
                szs=ak.stock_szse_summary(date=ds)
                sz_amt=0
                for nm in ["主板A股","创业板A股"]:
                    z=szs[szs["证券类别"].astype(str)==nm]
                    if not z.empty: sz_amt+=(safe_float(z.iloc[0]["成交金额"]) or 0)/1e8
                out.append({"date":d.isoformat(),"margin_balance":round(sh_bal+sz_bal,2),"turnover":round(sh_amt+sz_amt,2)})
                time.sleep(.15)
            except Exception as e:
                print("skip",ds,e)
        if out:
            data["liquidity"]={"status":"自动更新正常","series":out[-10:],"source":"上交所融资融券汇总 + 深交所融资融券汇总；成交额为沪市A股+科创板及深市主板A股+创业板A股"}
    except Exception as e:
        print("liquidity failed:",e)

def fetch_trade(data):
    url="https://data.mofcom.gov.cn/hwmy/imexmonth.shtml"
    try:
        html=requests.get(url,headers=UA,timeout=20).text
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(html,"html.parser")
        rows=[]
        for tr in soup.find_all("tr"):
            cells=[c.get_text(" ",strip=True) for c in tr.find_all(["td","th"])]
            if cells and re.match(r"^20\d{2}年\d{1,2}月$",cells[0]) and len(cells)>=11:
                rows.append({
                    "month_cn":cells[0],
                    "month":re.sub(r"年|月","-",cells[0]).rstrip("-").replace("-0","-"),
                    "total":safe_float(cells[1]),"total_yoy":safe_float(cells[2]),
                    "balance":safe_float(cells[4]),"exports":safe_float(cells[5]),
                    "export_yoy":safe_float(cells[6]),"imports":safe_float(cells[8]),
                    "import_yoy":safe_float(cells[9])
                })
        if rows:
            rows=sorted(rows,key=lambda x:datetime.strptime(x["month_cn"],"%Y年%m月"))
            latest=rows[-1]
            data["trade"]={
                "latest_month":latest["month_cn"],
                "release_note":"每日检查商务部官方页；只有官方页面出现新月份时才更新",
                "source":"商务部公共商务信息服务（页面标注：数据来源中国海关总署）",
                "series":[{k:r[k] for k in ["month","total","exports","imports","balance"]} for r in rows[-12:]],
                "latest":{k:latest[k] for k in ["total","exports","imports","balance","export_yoy","import_yoy"]}
            }
    except Exception as e:
        print("trade failed:",e)

METALS={
 "copper":{"name":"铜","symbol":"LME铜3个月","code":"CAD","unit":"美元/吨","drivers":["美国潜在精炼铜关税和跨市场套利","LME/COMEX库存变化与矿端扰动","全球电网、AI数据中心和中国需求"],"risks":["库存快速回补","关税预期落空","制造业需求转弱"]},
 "nickel":{"name":"镍","symbol":"LME镍3个月","code":"NID","unit":"美元/吨","drivers":["印尼矿石配额与冶炼原料供给","不锈钢和电池需求"],"risks":["LME和中国精炼镍高库存","印尼追加配额"]},
 "aluminum":{"name":"铝","symbol":"LME铝3个月","code":"AHD","unit":"美元/吨","drivers":["能源和氧化铝成本","海外供应扰动","汽车、电网和制造业需求"],"risks":["中国供应增长","全球工业需求走弱"]},
 "zinc":{"name":"锌","symbol":"LME锌3个月","code":"ZSD","unit":"美元/吨","drivers":["LME库存和海外现货紧张","矿山/冶炼扰动","欧美制造业"],"risks":["中国出口增加","挤仓缓解后多头平仓"]},
 "gold":{"name":"黄金","symbol":"COMEX黄金","code":"GC","unit":"美元/盎司","drivers":["美元与实际利率","央行和ETF需求","财政及地缘风险"],"risks":["实际利率上行","美元反弹","高位获利了结"]},
 "silver":{"name":"白银","symbol":"COMEX白银","code":"SI","unit":"美元/盎司","drivers":["贵金属资金流","工业需求","金银比修复"],"risks":["高波动和杠杆资金撤退","工业需求走弱"]}
}

def trend_text(vals):
    if len(vals)<6: return "数据不足","等待更多交易日后判断"
    last=vals[-1]; first=vals[0]
    pct=(last/first-1)*100 if first else 0
    ma5=sum(vals[-5:])/min(5,len(vals))
    ma20=sum(vals[-20:])/min(20,len(vals))
    mom5=(last/vals[-6]-1)*100 if len(vals)>=6 and vals[-6] else 0
    if pct>5 and last>ma5>=ma20: trend="强势上行"
    elif pct>1 and last>=ma20: trend="震荡偏强"
    elif pct<-5 and last<ma5<=ma20: trend="弱势下行"
    elif pct<-1 and last<ma20: trend="震荡偏弱"
    else: trend="震荡"
    if trend=="强势上行": out="未来1–4周技术面仍偏强，但高位波动会放大；若跌破20日均线，趋势降为中性。"
    elif trend=="震荡偏强": out="未来1–4周偏多震荡；若5日动量持续转正并站稳20日均线，上行概率提高。"
    elif trend=="震荡偏弱": out="未来1–4周偏弱震荡；需要重新站回20日均线才算明显改善。"
    elif trend=="弱势下行": out="未来1–4周技术面偏空；除非出现供给冲击或价格快速收复20日均线。"
    else: out="未来1–4周更可能区间震荡，等待价格对20日均线和近月高低点作出方向选择。"
    return trend,out,pct,mom5

def foreign_hist(code):
    try:
        df=ak.futures_foreign_hist(symbol=code)
        if df is not None and not df.empty:
            return pd.DataFrame({"date":pd.to_datetime(df["date"]),"close":pd.to_numeric(df["close"],errors="coerce")}).dropna()
    except Exception as e: print("foreign",code,e)
    return pd.DataFrame()

def comex_copper_hist_usd_ton():
    """Fallback for copper when the LME 3M history source is empty.

    Eastmoney HG00Y is COMEX copper quoted in USD/lb. Convert to USD per
    metric tonne so the website keeps the same unit as LME copper.
    """
    try:
        df=ak.futures_global_hist_em(symbol="HG00Y")
        if df is None or df.empty:
            return pd.DataFrame()
        out=pd.DataFrame({
            "date":pd.to_datetime(df["日期"]),
            "close":pd.to_numeric(df["最新价"],errors="coerce") * 2204.62262185
        }).dropna()
        return out
    except Exception as e:
        print("COMEX copper fallback failed:",e)
        return pd.DataFrame()

def fetch_metals(data):
    old=data.get("commodities",{})
    result={}
    for key,cfg in METALS.items():
        try:
            df=foreign_hist(cfg["code"])
            data_source="LME/COMEX primary source"
            if key=="copper":
                data_source="LME 3个月铜"
                if df is None or df.empty:
                    df=comex_copper_hist_usd_ton()
                    if df is not None and not df.empty:
                        data_source="COMEX铜连续备用（HG00Y，已折算美元/吨）"
            cutoff=pd.Timestamp.now().normalize()-pd.Timedelta(days=35)
            df=df[df["date"]>=cutoff].tail(30)
            vals=df["close"].astype(float).tolist()
            if vals:
                tr,out,pct,mom5=trend_text(vals)
                series=[{"date":d.strftime("%Y-%m-%d"),"close":round(float(v),4)} for d,v in zip(df["date"],df["close"])]
                item={**cfg,"latest":round(vals[-1],4),"month_change":round(pct,2),"trend":tr,"outlook":out,"series":series,"momentum_5d":round(mom5,2),"data_source":data_source}
                if key=="copper" and data_source.startswith("COMEX"):
                    item["symbol"]="COMEX铜连续（LME备用源）"
                    item["source_note"]="LME 3个月铜历史接口无数据时，自动切换东方财富 COMEX 铜连续 HG00Y；原始美元/磅已按 1 吨=2204.6226 磅折算为美元/吨。"
                elif key=="copper":
                    item["source_note"]="当前使用LME 3个月铜历史行情；若该接口为空会自动切换COMEX铜连续备用源。"
            else:
                item=old.get(key,{**cfg,"latest":None,"month_change":None,"trend":"数据不足","outlook":"等待行情源更新","series":[]})
                item.update({"drivers":cfg["drivers"],"risks":cfg["risks"],"unit":cfg["unit"]})
                if key=="copper":
                    item["symbol"]=cfg["symbol"]
                    item["data_source"]="LME主源与COMEX备用源本次均未返回数据"
                    item["source_note"]="铜已启用双数据源容错；本次若仍为空，请检查Actions日志中的LME与COMEX接口错误。"
                else:
                    item["symbol"]=cfg["symbol"]
            result[key]=item
        except Exception as e:
            print("metal failed",key,e)
            result[key]=old.get(key,{**cfg,"latest":None,"month_change":None,"trend":"更新失败","outlook":"本次行情源未返回数据","series":[]})
    # Add tungsten separately: it is a spot/contract benchmark, not an exchange future.
    result["tungsten"]=fetch_tungsten_public(old.get("tungsten"))
    data["commodities"]=result

def fetch_tungsten_public(old_item):
    """
    Tungsten has no LME-style continuous futures benchmark.
    Track public China APT (88.5% WO3) spot / long-term-contract references from SMM news.
    If scraping fails, preserve the last valid observation.
    """
    base = old_item or {
        "name":"钨",
        "symbol":"中国APT（88.5% WO₃）现货/长协参考",
        "unit":"元/吨",
        "latest":600000,
        "month_change":None,
        "trend":"高位震荡",
        "outlook":"等待更多公开报价更新",
        "drivers":["国内钨矿资源约束","出口管制与全球供应安全溢价","硬质合金、军工、航空航天和半导体需求"],
        "risks":["下游淡季需求偏弱","高价抑制补库","矿端供应恢复"],
        "series":[
            {"date":"2026-07-31","close":634000},
            {"date":"2026-08-05","close":606000},
            {"date":"2026-08-12","close":600000},
            {"date":"2026-08-14","close":605000},
            {"date":"2026-08-25","close":600000}
        ]
    }
    base["source_note"]="SMM公开钨市场资讯；钨无LME式连续期货，本项采用中国APT现货/长协公开报价。"
    try:
        index_url="https://news.metal.com/minor-metals/tungsten"
        html=requests.get(index_url,headers=UA,timeout=20).text
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(html,"html.parser")
        links=[]
        for a in soup.find_all("a",href=True):
            href=a["href"]
            if "/newscontent/" in href:
                if href.startswith("/"): href="https://news.metal.com"+href
                if href.startswith("http") and href not in links: links.append(href)
        latest_price=None
        latest_date=None
        # Inspect recent public news pages and look specifically for APT RMB/ton quotations.
        for url in links[:15]:
            try:
                page=requests.get(url,headers=UA,timeout=15).text
                ps=BeautifulSoup(page,"html.parser")
                body=ps.get_text(" ",strip=True)
                # Common SMM English patterns, e.g. "APT at 600,000 yuan/mt",
                # "APT was assessed at RMB 605,000 / ton".
                patterns=[
                    r'APT[^.]{0,180}?(?:at|to|around|assessed at|quoted at)[^\d]{0,30}(?:RMB|Yn|yuan)?\s*([0-9]{3},[0-9]{3})\s*(?:yuan|RMB|/mt|/ton|per ton)',
                    r'(?:RMB|Yn|yuan)\s*([0-9]{3},[0-9]{3})\s*(?:/mt|/ton|per ton)[^.]{0,120}?APT',
                    r'APT[^.]{0,180}?\s([0-9]{3},[0-9]{3})\s*yuan/mt'
                ]
                vals=[]
                for pat in patterns:
                    for m in re.finditer(pat,body,re.I):
                        v=int(m.group(1).replace(",",""))
                        if 100000 <= v <= 2000000:
                            vals.append(v)
                if vals:
                    # Prefer the last APT figure in the article; SMM often gives latest quote later in body.
                    latest_price=vals[-1]
                    dm=re.search(r'(?:Published:?\s*)?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),\s+(20\d{2})',body,re.I)
                    if dm:
                        latest_date=datetime.strptime(f"{dm.group(1)} {dm.group(2)} {dm.group(3)}","%b %d %Y").date().isoformat()
                    else:
                        latest_date=datetime.now().date().isoformat()
                    break
            except Exception:
                continue
        series=base.get("series",[])
        if latest_price:
            day=latest_date or datetime.now().date().isoformat()
            found=False
            for row in series:
                if row.get("date")==day:
                    row["close"]=latest_price
                    found=True
                    break
            if not found:
                series.append({"date":day,"close":latest_price})
            series=sorted(series,key=lambda x:x["date"])[-35:]
            vals=[float(x["close"]) for x in series]
            base["latest"]=latest_price
            base["series"]=series
            if len(vals)>=2:
                pct=(vals[-1]/vals[0]-1)*100
                base["month_change"]=round(pct,2)
                if pct>5: base["trend"]="强势上行"
                elif pct>1: base["trend"]="震荡偏强"
                elif pct<-5: base["trend"]="高位回落"
                elif pct<-1: base["trend"]="震荡偏弱"
                else: base["trend"]="高位横盘"
            base["outlook"]="未来1–4周重点看矿端供应约束与下游硬质合金订单的拉锯。若APT重新站上近期高点且钨精矿同步走强，趋势转强；若下游继续压价而APT跌破近月低点，则回落风险增加。"
    except Exception as e:
        print("tungsten public update failed:",e)
    return base

def main():
    data=load_data()
    fetch_liquidity(data)
    fetch_trade(data)
    fetch_metals(data)
    data["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M")
    DATA.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__": main()
