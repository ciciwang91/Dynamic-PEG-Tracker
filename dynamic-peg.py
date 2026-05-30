import os
import json
import re
from datetime import datetime
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup

# ==========================================
# 🔑 核心密钥与缓存配置 (给美股 FMP 引擎用)
# ==========================================
FMP_API_KEY = os.environ.get("FMP_API_KEY") 
CACHE_DIR = "./fmp_cache"

def get_fmp_historical_eps_with_cache(symbol, limit=10):
    """引擎 A (美股主战)：FMP 深度财务提取与本地额度保护"""
    if not FMP_API_KEY:
        print("  [!] 提示: 未检测到 FMP_API_KEY，跳过 FMP 请求。")
        return None
        
    # 💡 [关键修复]：自动清洗 GitHub Secrets 可能带来的隐形换行符和空格
    clean_key = FMP_API_KEY.strip()

    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)

    today_str = datetime.now().strftime("%Y-%m-%d")
    cache_filename = f"{CACHE_DIR}/eps_{symbol}_{today_str}.json"

    if os.path.exists(cache_filename):
        print(f"  -> 📂 [缓存命中] 从本地读取 {symbol} 财务，消耗 0 额度。")
        with open(cache_filename, 'r', encoding='utf-8') as f:
            return json.load(f)

    url = f"https://financialmodelingprep.com/api/v3/income-statement/{symbol}?period=quarter&limit={limit}&apikey={clean_key}"
    
    try:
        # 打印脱敏后的 Key，确认到底传了什么过去
        safe_key = f"{clean_key[:5]}***{clean_key[-3:]}" if len(clean_key) > 8 else "INVALID"
        print(f"  -> 🌐 [FMP请求] 使用 Key: {safe_key}")
        
        response = requests.get(url, timeout=10)
        
        # 💡 [不再静默]：强行打印 FMP 的真实嘴脸
        print(f"  -> 🐞 [DEBUG] FMP 状态码: {response.status_code}")
        if response.status_code != 200:
            print(f"  -> 🐞 [DEBUG] FMP 报错详情: {response.text[:250]}")
            return None
            
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            valid_eps = [item.get('eps') for item in data if item.get('eps') is not None]
            with open(cache_filename, 'w', encoding='utf-8') as f:
                json.dump(valid_eps, f, ensure_ascii=False, indent=4)
            return valid_eps
            
        print(f"  -> 🐞 [DEBUG] FMP 没报错，但返回了空数据或格式不对: {str(data)[:250]}")
        return None
    except Exception as e:
        print(f"  [!] FMP 接口异常: {e}")
        return None

def get_naver_valuation_and_growth(symbol):
    """引擎 B (韩股主战)：Naver 深度爬虫，直接抓表格算增速"""
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
        for th in soup.find_all('th'):
            if 'EPS(원)' in th.text:
                eps_row = th.parent
                break
        
        if eps_row:
            raw_eps = []
            for td in eps_row.find_all('td'):
                val = td.text.strip().replace(',', '')
                if val and re.match(r'^-?\d+(\.\d+)?$', val):
                    raw_eps.append(float(val))
            
            # 提取最后几个季度的数字，对比今年和去年同期
            if len(raw_eps) >= 5:
                latest_q_eps = raw_eps[-1]
                last_y_q_eps = raw_eps[-5]
                if latest_q_eps > 0 and last_y_q_eps > 0:
                    growth_pct = ((latest_q_eps - last_y_q_eps) / last_y_q_eps) * 100

        return pe_val, pb_val, growth_pct
    except Exception as e:
        print(f"  [!] Naver 爬取失败: {e}")
        return None, None, None

def fetch_stock_data(symbol):
    try:
        print(f"\n正在获取 {symbol} 的数据...")
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        current_price = info.get('currentPrice', info.get('regularMarketPrice'))
        pb_ratio = info.get('priceToBook')
        trailing_pe = info.get('trailingPE')
        forward_pe = info.get('forwardPE')
        static_peg = info.get('pegRatio')
        
        dynamic_growth_pct = None
        dynamic_peg = None
        data_source = "Yahoo (兜底)"

        # 💡 [智能分流核心] 
        if symbol.endswith('.KS'):
            # 🇰🇷 韩股：直接绕过 FMP，走 Naver 爬虫
            print(f"  -> 🧭 路由: 检测为韩股，启动 Naver 深度抓取...")
            naver_pe, naver_pb, naver_growth = get_naver_valuation_and_growth(symbol)
            if naver_pe: trailing_pe = naver_pe
            if naver_pb: pb_ratio = naver_pb
            if naver_growth: 
                dynamic_growth_pct = naver_growth
                data_source = "Naver"
                print(f"  -> 📈 [Naver] 成功计算韩股真实增速: {dynamic_growth_pct:.2f}%")
        else:
            # 🇺🇸 美股：坚决使用 FMP 获取完美 8 季度滚动 TTM
            print(f"  -> 🧭 路由: 检测为美股，启动 FMP 深度财务引擎...")
            historical_eps = get_fmp_historical_eps_with_cache(symbol, limit=10)
            if historical_eps and len(historical_eps) >= 8:
                current_ttm_eps = sum(historical_eps[0:4])
                prior_ttm_eps = sum(historical_eps[4:8])
                if current_ttm_eps > 0 and prior_ttm_eps > 0:
                    dynamic_growth_pct = ((current_ttm_eps - prior_ttm_eps) / prior_ttm_eps) * 100
                    trailing_pe = current_price / current_ttm_eps
                    data_source = "FMP (8季滚动)"
                    print(f"  -> 💎 [FMP] 完美获取 8 季度数据！算出滚动增速: {dynamic_growth_pct:.2f}%")
            else:
                # FMP 万一抽风，雅虎兜底
                earnings_growth = info.get('earningsGrowth')
                if earnings_growth:
                    dynamic_growth_pct = earnings_growth * 100
        
        # 结算有效 PEG
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
            "Dyn. Growth": f"{round(dynamic_growth_pct, 2)}%" if dynamic_growth_pct else "N/A",
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
        if pb >= 2.4: alerts.append(f"🔴 [{symbol} 危险] P/B = {pb:.2f} 已达周期历史极高位！")
        elif pb >= 2.0: alerts.append(f"🟠 [{symbol} 预警] P/B = {pb:.2f} 进入高估值区间。")
    if peg and isinstance(peg, (int, float)):
        if peg > 2.0: alerts.append(f"🟡 [{symbol} 过热] 动态 PEG = {peg:.2f}，估值透支。")
        elif 0 < peg <= 1.0: alerts.append(f"🟢 [{symbol} 价值] 动态 PEG = {peg:.2f}，黄金击球区！")
    return alerts

if __name__ == "__main__":
    symbols_to_track = ["000660.KS", "005930.KS", "MU", "NVDA","LITE"] 
    all_results = []
    all_alerts = []
    
    print("="*100)
    print(" 周期股估值多维监控系统 (FMP 美股 + Naver 韩股 智能路由版)")
    print("="*100)
    
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
        
        print("\n💡 【系统策略分析】")
        for alert in all_alerts: print(alert)
        if not all_alerts: print("🟢 所有监控标的估值均未触及高危预警线。")
            
        filename = "valuation_log.csv"
        file_exists = os.path.isfile(filename)
        df.to_csv(filename, mode='a', index=False, header=not file_exists)
        print(f"\n✅ 数据已成功落库至 {filename}")
