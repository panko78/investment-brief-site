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

def fetch_metals(data):
    old=data.get("commodities",{})
    result={}
    for key,cfg in METALS.items():
        try:
            df=foreign_hist(cfg["code"])
            cutoff=pd.Timestamp.now().normalize()-pd.Timedelta(days=35)
            df=df[df["date"]>=cutoff].tail(30)
            vals=df["close"].astype(float).tolist()
            if vals:
                tr,out,pct,mom5=trend_text(vals)
                series=[{"date":d.strftime("%Y-%m-%d"),"close":round(float(v),4)} for d,v in zip(df["date"],df["close"])]
                item={**cfg,"latest":round(vals[-1],4),"month_change":round(pct,2),"trend":tr,"outlook":out,"series":series,"momentum_5d":round(mom5,2)}
            else:
                item=old.get(key,{**cfg,"latest":None,"month_change":None,"trend":"数据不足","outlook":"等待行情源更新","series":[]})
                item.update({"drivers":cfg["drivers"],"risks":cfg["risks"],"symbol":cfg["symbol"],"unit":cfg["unit"]})
            result[key]=item
        except Exception as e:
            print("metal failed",key,e)
            result[key]=old.get(key,{**cfg,"latest":None,"month_change":None,"trend":"更新失败","outlook":"本次行情源未返回数据","series":[]})
    data["commodities"]=result

def main():
    data=load_data()
    fetch_liquidity(data)
    fetch_trade(data)
    fetch_metals(data)
    data["updated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M")
    DATA.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__": main()
