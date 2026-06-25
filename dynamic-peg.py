import os
from datetime import datetime, timezone
import re
import pandas as pd
import requests
from bs4 import BeautifulSoup
import yfinance as yf

def check_if_market_open(symbol):
    """
    智能休市拦截器：探测最新 K 线日期。如果是手动测试，直接放行。
    """
    # 💡 官方后门：检测如果是你手动点击运行的，直接无视休市规则！
    if os.environ.get("TRIGGER_REASON") == "workflow_dispatch":
        print(f"  [手动测试] 🚀 检测到手动触发，无视 {symbol} 休市状态，强制放行！")
        return True

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d")
        if hist.empty:
            return True 
            
        last_trade_date = hist.index[0].date()
        today_utc = datetime.now(timezone.utc).date()
        
        if last_trade_date != today_utc:
            print(f"  [休市拦截] 🛑 {symbol} 最新交易停留在 {last_trade_date}。今日为休市日，跳过。")
            return False
        return True
    except Exception:
        return True

def get_naver_valuation_and_growth(symbol):
    """韩国本土爬虫：抓取 PE、PB 及最新一季 EPS 同比增速"""
    code = symbol.replace('.KS', '')
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    pe_val, pb_val, growth_pct = None, None, None
    try:
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        per_tag = soup.select_one('#_per')
        pbr_tag = soup.select_one('#_pbr')
        if per_tag: pe_val = float(per_tag.text.replace(',', ''))
        if pbr_tag: pb_val = float(pbr_tag.text.replace(',', ''))

        eps_row = None
        for th in soup.find_all('th', class_=re.compile('.*')):
            if 'EPS(원)' in th.text:
                eps_row = th.parent
                break
        
        if not eps_row:
            for tr in soup.find_all('tr'):
                th = tr.find('th')
                if th and 'EPS(원)' in th.text:
                    eps_row = tr
                    break

        if eps_row:
            raw_eps = []
            for td in eps_row.find_all('td'):
                val = td.text.strip().replace(',', '')
                if val and re.match(r'^-?\d+(\.\d+)?$', val):
                    raw_eps.append(float(val))
            
            if len(raw_eps) >= 5:
                latest_q_eps = raw_eps[-1]
                last_y_q_eps = raw_eps[-5]
                if latest_q_eps > 0 and last_y_q_eps > 0:
                    growth_pct = ((latest_q_eps - last_y_q_eps) / last_y_q_eps) * 100

        return pe_val, pb_val, growth_pct
    except Exception:
        return None, None, None

def fetch_stock_data(symbol):
    """自适应数据抓取引擎"""
    print(f"\n正在获取 {symbol} 的数据...")
    
    # 💡 核心优化：先过安检，遇到节假日直接踢出
    if not check_if_market_open(symbol):
        return None, None, None
        
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        current_price = info.get('currentPrice', info.get('regularMarketPrice'))
        pb_ratio = info.get('priceToBook')
        trailing_pe = info.get('trailingPE')
        forward_pe = info.get('forwardPE')
        static_peg = info.get('pegRatio')
        
        dynamic_growth_pct = None
        dynamic_peg = None
        data_source = "N/A"
        
        if symbol.endswith('.KS'):
            print(f"  -> 启动 Naver 深度爬虫...")
            naver_pe, naver_pb, naver_growth = get_naver_valuation_and_growth(symbol)
            if naver_pe: trailing_pe = naver_pe
            if naver_pb: pb_ratio = naver_pb
            if naver_growth: 
                dynamic_growth_pct = naver_growth
                data_source = "Naver (单季同比)"
        else:
            try:
                q_stmt = ticker.quarterly_income_stmt
                eps_row = None
                for row_name in ['Basic EPS', 'Diluted EPS', 'BasicEPS', 'DilutedEPS']:
                    if row_name in q_stmt.index:
                        eps_row = q_stmt.loc[row_name].dropna()
                        break
                
                if eps_row is not None:
                    if len(eps_row) >= 8:
                        current_ttm_eps = eps_row.iloc[0:4].sum()
                        prior_ttm_eps = eps_row.iloc[4:8].sum()
                        if current_ttm_eps > 0 and prior_ttm_eps > 0:
                            dynamic_growth_pct = ((current_ttm_eps - prior_ttm_eps) / prior_ttm_eps) * 100
                            data_source = "Yahoo (8季 TTM)"
                            print(f"  -> 💎 惊喜！成功拉取 8 季度数据，启用 TTM 滚动增速。")
                    elif len(eps_row) >= 5:
                        q1_eps = eps_row.iloc[0] 
                        q5_eps = eps_row.iloc[4] 
                        if q1_eps > 0 and q5_eps > 0:
                            dynamic_growth_pct = ((q1_eps - q5_eps) / q5_eps) * 100
                            data_source = "Yahoo (5季同比)"
                            
                if dynamic_growth_pct is None and info.get('earningsGrowth'):
                    dynamic_growth_pct = info.get('earningsGrowth') * 100
                    data_source = "Yahoo (预期兜底)"
            except Exception:
                pass

        if dynamic_growth_pct and trailing_pe and dynamic_growth_pct > 0:
            dynamic_peg = trailing_pe / dynamic_growth_pct
            
        currency = info.get('currency', 'KRW' if symbol.endswith('.KS') else 'USD')
        formatted_price = f"{current_price} {currency}" if current_price else "N/A"
        
        data = {
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Symbol": symbol,
            "Price": formatted_price, 
            "P/B": round(pb_ratio, 2) if pb_ratio else "N/A",
            "PE(TTM)": round(trailing_pe, 2) if trailing_pe else "N/A",
            "PE(Fwd)": round(forward_pe, 2) if forward_pe else "N/A",
            "Growth": f"{round(dynamic_growth_pct, 2)}%" if dynamic_growth_pct else "N/A",
            "Static PEG": round(static_peg, 3) if (static_peg and static_peg > 0) else "N/A",
            "Dynamic PEG": round(dynamic_peg, 3) if dynamic_peg else "N/A",
            "Source": data_source
        }
        return data, pb_ratio, dynamic_peg

    except Exception as e:
        print(f"❌ 获取 {symbol} 数据失败: {e}")
        return None, None, None

def analyze_signals(symbol, pb, peg):
    alerts = []
    if pb and isinstance(pb, (int, float)):
        if pb >= 2.4: alerts.append(f"🔴 [{symbol}] P/B = {pb:.2f} 极高风险。")
        elif pb >= 2.0: alerts.append(f"🟠 [{symbol}] P/B = {pb:.2f} 估值偏高。")
    if peg and isinstance(peg, (int, float)):
        if peg > 2.0: alerts.append(f"🟡 [{symbol}] 动态 PEG = {peg:.2f}，注意回撤。")
        elif 0 < peg <= 1.0: alerts.append(f"🟢 [{symbol}] 动态 PEG = {peg:.2f}，黄金买点！")
    return alerts

if __name__ == "__main__":
    symbols_to_track = ["000660.KS", "005930.KS", "MU", "NVDA", "MRVL","COHR"] 
    all_results = []
    all_alerts = []
    
    print("="*95)
    print(" 周期股估值多维监控系统 (带节假日智能拦截)")
    print("="*95)
    
    for sym in symbols_to_track:
        result_data, current_pb, active_peg = fetch_stock_data(sym)
        if result_data:
            
            all_results.append(result_data)
            all_alerts.extend(analyze_signals(sym, current_pb, active_peg))
            
    if all_results:
        df = pd.DataFrame(all_results)
        df = df.fillna("N/A") 
        pd.set_option('display.unicode.ambiguous_as_wide', True)
        pd.set_option('display.unicode.east_asian_width', True)
        pd.set_option('display.width', 1000)
        
        print("\n📊 【今日有效数据概览】")
        print(df.to_string(index=False))
        
        for alert in all_alerts: print(alert)
        
        # 存入表格
        import os
        filename = "valuation_log.csv"
        file_exists = os.path.isfile(filename)
        df.to_csv(filename, mode='a', index=False, header=not file_exists)
        print(f"\n✅ 数据已成功落库至 {filename}")
    else:
        print("\n🔵 今日所有监控标的均休市，未生成任何冗余数据。")
