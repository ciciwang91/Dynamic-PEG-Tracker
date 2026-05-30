import os
import json
from datetime import datetime
import yfinance as yf
import pandas as pd
import requests

# ==========================================
# 🔑 配置你的数据源密钥
# ==========================================
FMP_API_KEY = "qDij1oWnQwowmrpBlZxKQkYVfNnIxuat"  # <--- 请替换为你注册获取的真实 Key
CACHE_DIR = "./fmp_cache"                   # 缓存文件夹路径

def get_fmp_historical_eps_with_cache(symbol, limit=10):
    """
    带本地缓存功能的 FMP EPS 获取函数（极度节省额度）
    """
    if FMP_API_KEY == "在这里填入你的_FMP_API_KEY" or not FMP_API_KEY:
        print("  [!] 错误: 未配置 FMP API Key，无法获取深度历史数据。")
        return None

    # 创建缓存目录
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)

    # 构造以当天日期命名的缓存文件名
    today_str = datetime.now().strftime("%Y-%m-%d")
    cache_filename = f"{CACHE_DIR}/eps_{symbol}_{today_str}.json"

    # 🎯 核心额度保护：如果今天已经下载过，直接读取本地文件，消耗 0 额度
    if os.path.exists(cache_filename):
        print(f"  -> 📂 [本地缓存命中] 成功从本地读取 {symbol} 今日数据，消耗 0 FMP 额度。")
        with open(cache_filename, 'r', encoding='utf-8') as f:
            return json.load(f)

    # 如果本地没有，才发起网络请求（消耗 1 次额度）
    url = f"https://financialmodelingprep.com/api/v3/income-statement/{symbol}?period=quarter&limit={limit}&apikey={FMP_API_KEY}"
    
    try:
        print(f"  -> 🌐 [网络请求发起] 正在向 FMP 请求 {symbol} 深度财报（消耗 1 次额度）...")
        response = requests.get(url, timeout=10)
        
        # 严格检查状态码
        if response.status_code != 200:
            print(f"  [!] FMP 服务器返回错误状态码: {response.status_code}")
            return None
            
        data = response.json()
        
        # 检查 FMP 是否返回了错误信息（比如 Key 无效或额度用光）
        if isinstance(data, dict) and "Error Message" in data:
            print(f"  [!] FMP API 报错: {data['Error Message']}")
            return None

        if isinstance(data, list) and len(data) > 0:
            valid_eps = [item.get('eps') for item in data if item.get('eps') is not None]
            
            # 写入本地缓存，供今天后续运行使用
            with open(cache_filename, 'w', encoding='utf-8') as f:
                json.dump(valid_eps, f, ensure_ascii=False, indent=4)
                
            return valid_eps
        else:
            print(f"  [!] FMP 未返回有效列表数据，无法建立缓存。")
            return None
            
    except Exception as e:
        print(f"  [!] FMP 接口请求发生异常: {e}")
        return None

def fetch_valuation_pro(symbol):
    """专业版抓取：yfinance 获取实时快照 + 缓存版 FMP 提供深度财报"""
    try:
        print(f"正在获取 {symbol} 的数据...")
        
        # 1. 用 yfinance 拿基础快照 (不费 API 额度)
        ticker = yf.Ticker(symbol)
        info = ticker.info
        current_price = info.get('currentPrice', info.get('regularMarketPrice'))
        pb_ratio = info.get('priceToBook')
        forward_pe = info.get('forwardPE')
        static_peg = info.get('pegRatio')
        
        trailing_pe = info.get('trailingPE')
        dynamic_growth_pct = None
        dynamic_peg = None
        
        # 2. 呼叫带缓存保护的 FMP 引擎获取 EPS
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
            alerts.append(f"🔴 [{symbol} 危险] P/B = {pb:.2f} 已达周期极限高位，建议清仓保护利润！")
        elif pb >= 2.0:
            alerts.append(f"🟠 [{symbol} 预警] P/B = {pb:.2f} 进入高估值区，建议结合技术面分批止盈。")
    if peg and isinstance(peg, (int, float)):
        if peg > 2.0: 
            alerts.append(f"🟡 [{symbol} 过热] 有效 PEG = {peg:.2f}，估值扩张过快。")
        elif 0 < peg <= 1.0:
            alerts.append(f"🟢 [{symbol} 价值] 有效 PEG = {peg:.2f}，处于高增长且估位合理的黄金买点。")
    return alerts

if __name__ == "__main__":
    symbols_to_track = ["000660.KS", "005930.KS", "MU", "NVDA"] 
    all_results = []
    all_alerts = []
    
    print("="*90)
    print(" 周期股估值多维监控系统 (Yahoo + FMP 混合专业缓存版)")
    print("="*90)
    
    for sym in symbols_to_track:
        result_data, current_pb, active_peg = fetch_valuation_pro(sym)
        if result_data:
            all_results.append(result_data)
            alerts = analyze_signals(sym, current_pb, active_peg)
            all_alerts.extend(alerts)
            
    if all_results:
        print("\n📊 【今日数据概览】")
        pd.set_option('display.unicode.ambiguous_as_wide', True)
        pd.set_option('display.unicode.east_asian_width', True)
        pd.set_option('display.width', 1000)
        
        df = pd.DataFrame(all_results)
        df = df.fillna("N/A") 
        print(df.to_string(index=False))
        
        print("\n💡 【系统策略分析】")
        if all_alerts:
            for alert in all_alerts:
                print(alert)
        else:
            print("🟢 所有标的估值正常。")
