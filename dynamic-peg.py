import yfinance as yf
import pandas as pd
from datetime import datetime
import os
import requests

# ==========================================
# 🔑 配置你的数据源密钥
# ==========================================
FMP_API_KEY = "qDij1oWnQwowmrpBlZxKQkYVfNnIxuat"  # <--- 请替换为你注册获取的真实 Key

def get_fmp_historical_eps(symbol, limit=10):
    """
    通过 FMP 接口获取过去 N 个季度的真实 EPS 数据
    """
    if FMP_API_KEY == "在这里填入你的_FMP_API_KEY":
        print("  [!] 警告: 未配置 FMP API Key，无法获取深度历史数据。")
        return None

    # FMP 接口 URL (income-statement 按季度)
    url = f"https://financialmodelingprep.com/api/v3/income-statement/{symbol}?period=quarter&limit={limit}&apikey={FMP_API_KEY}"
    
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        
        # 检查是否返回了有效的列表数据
        if isinstance(data, list) and len(data) > 0:
            # 提取每个季度的 eps (FMP 的数据默认是从最新到最旧排列)
            valid_eps = [item.get('eps') for item in data if item.get('eps') is not None]
            return valid_eps
        else:
            return None
    except Exception as e:
        print(f"  [!] FMP 接口请求失败: {e}")
        return None

def fetch_valuation_pro(symbol):
    """专业版抓取：yfinance 获取实时快照 + FMP 提供深度财报"""
    try:
        print(f"正在获取 {symbol} 的数据...")
        
        # 1. 用 yfinance 拿基础快照 (不费 API 额度)
        ticker = yf.Ticker(symbol)
        info = ticker.info
        current_price = info.get('currentPrice', info.get('regularMarketPrice'))
        pb_ratio = info.get('priceToBook')
        forward_pe = info.get('forwardPE')
        static_peg = info.get('pegRatio')
        
        # 2. 初始化我们要手搓的动态数据
        trailing_pe = info.get('trailingPE')
        dynamic_growth_pct = None
        dynamic_peg = None
        
        # 💡 【核心换擎】：直接呼叫 FMP 获取过去 10 个季度的 EPS
        historical_eps = get_fmp_historical_eps(symbol, limit=10)
        
        if historical_eps and len(historical_eps) >= 8:
            # 过去 1-4 季度 (今年 TTM)
            current_ttm_eps = sum(historical_eps[0:4])
            # 过去 5-8 季度 (去年 TTM)
            prior_ttm_eps = sum(historical_eps[4:8])
            
            if current_ttm_eps > 0 and prior_ttm_eps > 0:
                # 算出最真实的 TTM 增速
                dynamic_growth_pct = ((current_ttm_eps - prior_ttm_eps) / prior_ttm_eps) * 100
                # 基于 FMP 真实利润重算当前 PE
                trailing_pe = current_price / current_ttm_eps
                
                if dynamic_growth_pct > 0:
                    dynamic_peg = trailing_pe / dynamic_growth_pct
                    print(f"  -> 💎 [FMP 引擎] 完美获取 8+ 季度数据! 动态 PEG: {dynamic_peg:.3f}")
        
        elif historical_eps and len(historical_eps) >= 5:
             # FMP 兜底逻辑：如果连 FMP 也只给出了 5-7 个季度（比如刚上市不久），则降级使用单季同比
             latest_quarter_eps = historical_eps[0]
             last_year_quarter_eps = historical_eps[4]
             if latest_quarter_eps > 0 and last_year_quarter_eps > 0:
                 dynamic_growth_pct = ((latest_quarter_eps - last_year_quarter_eps) / last_year_quarter_eps) * 100
                 if trailing_pe and dynamic_growth_pct > 0:
                     dynamic_peg = trailing_pe / dynamic_growth_pct
                     print(f"  -> ⚠️ [FMP 降级] 数据不足8季，使用单季同比算出动态 PEG")
        else:
             print("  -> ❌ [FMP 引擎] 未获取到足够的历史利润数据。")

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
            alerts.append(f"🟡 [{symbol} 过热] 有效 PEG = {peg:.2f}，估值扩张过快，请关注拐点。")
        elif 0 < peg <= 1.0:
            alerts.append(f"🟢 [{symbol} 价值] 有效 PEG = {peg:.2f}，处于高增长且估位合理的黄金买点。")
    return alerts

if __name__ == "__main__":
    # 提醒：FMP 支持韩股，通常直接用 000660.KS 即可，如果报错，可以尝试去掉 .KS
    symbols_to_track = ["000660.KS", "005930.KS", "MU", "NVDA"] 
    
    all_results = []
    all_alerts = []
    
    print("="*90)
    print(" 周期股估值多维监控系统 (Yahoo + FMP 混合专业版)")
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
