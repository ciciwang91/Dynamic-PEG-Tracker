import os
import json
from datetime import datetime
import yfinance as yf
import pandas as pd
import requests

# ==========================================
# 🔑 安全获取环境变量中的数据源密钥
# ==========================================
# 这样写的好处：无论是本地运行(提前 export 变量)还是在 GitHub Actions 里，都能自动读取
FMP_API_KEY = os.environ.get("FMP_API_KEY") 
CACHE_DIR = "./fmp_cache"

def get_fmp_historical_eps_with_cache(symbol, limit=10):
    """带本地/仓库缓存功能的 FMP EPS 获取函数"""
    if not FMP_API_KEY:
        print("  [!] 警告: 未检测到环境变量 FMP_API_KEY，跳过深度历史数据拉取。")
        return None

    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)

    today_str = datetime.now().strftime("%Y-%m-%d")
    cache_filename = f"{CACHE_DIR}/eps_{symbol}_{today_str}.json"

    # 如果 Actions 从仓库里拉下来了今天的缓存，直接读取，0 消耗
    if os.path.exists(cache_filename):
        print(f"  -> 📂 [缓存命中] 从仓库缓存读取 {symbol} 今日数据，消耗 0 额度。")
        with open(cache_filename, 'r', encoding='utf-8') as f:
            return json.load(f)

    url = f"https://financialmodelingprep.com/api/v3/income-statement/{symbol}?period=quarter&limit={limit}&apikey={FMP_API_KEY}"
    
    try:
        print(f"  -> 🌐 [网络请求] 正在向 FMP 请求 {symbol} 深度财报（消耗 1 次额度）...")
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            return None
            
        data = response.json()
        if isinstance(data, dict) and "Error Message" in data:
            print(f"  [!] FMP API 报错: {data['Error Message']}")
            return None

        if isinstance(data, list) and len(data) > 0:
            valid_eps = [item.get('eps') for item in data if item.get('eps') is not None]
            with open(cache_filename, 'w', encoding='utf-8') as f:
                json.dump(valid_eps, f, ensure_ascii=False, indent=4)
            return valid_eps
        return None
            
    except Exception as e:
        print(f"  [!] FMP 接口异常: {e}")
        return None

def fetch_valuation_pro(symbol):
    try:
        print(f"正在获取 {symbol} 的数据...")
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        current_price = info.get('currentPrice', info.get('regularMarketPrice'))
        pb_ratio = info.get('priceToBook')
        forward_pe = info.get('forwardPE')
        static_peg = info.get('pegRatio')
        trailing_pe = info.get('trailingPE')
        
        dynamic_growth_pct = None
        dynamic_peg = None
        
        historical_eps = get_fmp_historical_eps_with_cache(symbol, limit=10)
        
        if historical_eps and len(historical_eps) >= 8:
            current_ttm_eps = sum(historical_eps[0:4])
            prior_ttm_eps = sum(historical_eps[4:8])
            if current_ttm_eps > 0 and prior_ttm_eps > 0:
                dynamic_growth_pct = ((current_ttm_eps - prior_ttm_eps) / prior_ttm_eps) * 100
                trailing_pe = current_price / current_ttm_eps
                if dynamic_growth_pct > 0:
                    dynamic_peg = trailing_pe / dynamic_growth_pct
        elif historical_eps and len(historical_eps) >= 5:
             latest_quarter_eps = historical_eps[0]
             last_year_quarter_eps = historical_eps[4]
             if latest_quarter_eps > 0 and last_year_quarter_eps > 0:
                 dynamic_growth_pct = ((latest_quarter_eps - last_year_quarter_eps) / last_year_quarter_eps) * 100
                 if trailing_pe and dynamic_growth_pct > 0:
                     dynamic_peg = trailing_pe / dynamic_growth_pct

        currency = info.get('currency', 'USD')
        data = {
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Symbol": symbol,
            f"Price ({currency})": current_price if current_price else "N/A",
            "P/B": round(pb_ratio, 2) if pb_ratio else "N/A",
            "PE(TTM)": round(trailing_pe, 2) if trailing_pe else "N/A",
            "PE(Fwd)": round(forward_pe, 2) if forward_pe else "N/A",
            "TTM Growth": f"{round(dynamic_growth_pct, 2)}%" if dynamic_growth_pct else "N/A",
            "Static PEG": round(static_peg, 3) if (static_peg and static_peg > 0) else "N/A",
            "Dynamic PEG": round(dynamic_peg, 3) if dynamic_peg else "N/A"
        }
        return data, pb_ratio, dynamic_peg if dynamic_peg else static_peg

    except Exception as e:
        print(f"❌ 获取 {symbol} 数据失败: {e}")
        return None, None, None

def analyze_signals(symbol, pb, peg):
    alerts = []
    if pb and isinstance(pb, (int, float)):
        if pb >= 2.4:
            alerts.append(f"🔴 [{symbol} 危险] P/B = {pb:.2f} 极度高估！")
        elif pb >= 2.0:
            alerts.append(f"🟠 [{symbol} 预警] P/B = {pb:.2f} 进入高估区。")
    if peg and isinstance(peg, (int, float)):
        if peg > 2.0: 
            alerts.append(f"🟡 [{symbol} 过热] PEG = {peg:.2f}，估值过快。")
        elif 0 < peg <= 1.0:
            alerts.append(f"🟢 [{symbol} 价值] PEG = {peg:.2f}，黄金买点。")
    return alerts

if __name__ == "__main__":
    symbols_to_track = ["000660.KS", "005930.KS", "MU", "NVDA"] 
    all_results = []
    all_alerts = []
    
    print("="*90)
    print(" 周期股估值多维监控系统 (GitHub Actions 自动化部署版)")
    print("="*90)
    
    for sym in symbols_to_track:
        result_data, current_pb, active_peg = fetch_valuation_pro(sym)
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
        for alert in all_alerts:
            print(alert)
        if not all_alerts:
            print("🟢 所有标的估值正常。")
            
        # 存入本地/仓库，供 Actions 抓取提交
        filename = "valuation_log.csv"
        file_exists = os.path.isfile(filename)
        df.to_csv(filename, mode='a', index=False, header=not file_exists)
        print(f"\n✅ 数据已成功存入 {filename}")
