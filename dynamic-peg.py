from datetime import datetime
import re
import pandas as pd
import requests
from bs4 import BeautifulSoup
import yfinance as yf

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
    """榨干雅虎价值引擎：尝试 8 季 TTM -> 降级 5 季同比 -> 韩股 Naver"""
    try:
        print(f"正在获取 {symbol} 的数据...")
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
            # 💡 [雅虎榨干引擎]
            try:
                # 获取尽可能多的季度财报（默认可能只有 4-5 季，但有时会给更多）
                q_stmt = ticker.quarterly_income_stmt
                eps_row = None
                for row_name in ['Basic EPS', 'Diluted EPS', 'BasicEPS', 'DilutedEPS']:
                    if row_name in q_stmt.index:
                        eps_row = q_stmt.loc[row_name].dropna()
                        break
                
                if eps_row is not None:
                    # 筛网 1：如果大发慈悲给了 8 季度以上，算完美 TTM 滚动
                    if len(eps_row) >= 8:
                        # 雅虎的数据通常从新到旧排列
                        current_ttm_eps = eps_row.iloc[0:4].sum()
                        prior_ttm_eps = eps_row.iloc[4:8].sum()
                        if current_ttm_eps > 0 and prior_ttm_eps > 0:
                            dynamic_growth_pct = ((current_ttm_eps - prior_ttm_eps) / prior_ttm_eps) * 100
                            data_source = "Yahoo (8季 TTM)"
                            print(f"  -> 💎 惊喜！雅虎给足了 8 季度，成功计算 TTM 滚动增速。")
                            
                    # 筛网 2：如果只有 5-7 个季度，算单季同比兜底
                    elif len(eps_row) >= 5:
                        q1_eps = eps_row.iloc[0] # 最新季
                        q5_eps = eps_row.iloc[4] # 去年同期
                        if q1_eps > 0 and q5_eps > 0:
                            dynamic_growth_pct = ((q1_eps - q5_eps) / q5_eps) * 100
                            data_source = "Yahoo (5季同比)"
                            
                # 筛网 3：雅虎抠搜到连 5 季度都不给的终极兜底
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
    symbols_to_track = ["000660.KS", "005930.KS", "MU", "NVDA", "LITE"] 
    all_results = []
    all_alerts = []
    
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
        
        print("\n📊 【今日数据概览】")
        print(df.to_string(index=False))
        
        for alert in all_alerts: print(alert)
