import yfinance as yf
import pandas as pd
from datetime import datetime
import os
import requests
from bs4 import BeautifulSoup
import numpy as np

def get_naver_data(symbol):
    """专门针对韩股的爬虫补丁，从 Naver Finance 抓取真实的 PE 和 PB"""
    code = symbol.replace('.KS', '')
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        per_tag = soup.select_one('#_per')
        pbr_tag = soup.select_one('#_pbr')
        
        pe_val = float(per_tag.text.replace(',', '')) if per_tag else None
        pb_val = float(pbr_tag.text.replace(',', '')) if pbr_tag else None
        
        return pe_val, pb_val
    except Exception as e:
        print(f"  [!] Naver 补充爬取失败: {e}")
        return None, None

def fetch_valuation(symbol):
    """抓取股票估值数据 (支持雅虎 8季度 TTM 滚动融合 + Naver 混合补丁)"""
    try:
        print(f"正在获取 {symbol} 的数据...")
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        current_price = info.get('currentPrice', info.get('regularMarketPrice'))
        pb_ratio = info.get('priceToBook')
        trailing_pe = info.get('trailingPE')
        forward_pe = info.get('forwardPE')
        
        # 1. 静态数据 (来自雅虎的瞬时快照)
        static_peg = info.get('pegRatio')
        static_growth = info.get('earningsGrowth') 
        
        # 2. 动态数据 (我们自己计算的 TTM 滚动数据，先设为空)
        dynamic_peg = None
        dynamic_growth_pct = None
        
        # 💡 【核心黑科技】：尝试提取 8 季度的财务报表，计算平滑的 TTM 动态 PEG
        try:
            q_financials = ticker.quarterly_financials
            eps_row = None
            for row_name in ['Basic EPS', 'Diluted EPS', 'BasicEPS', 'DilutedEPS']:
                if row_name in q_financials.index:
                    eps_row = q_financials.loc[row_name]
                    break
            
            if eps_row is not None and len(eps_row) >= 8:
                current_ttm_eps = sum(eps_row.iloc[0:4])
                prior_ttm_eps = sum(eps_row.iloc[4:8])
                
                if current_ttm_eps > 0 and prior_ttm_eps > 0:
                    # 计算滚动 TTM 增速 %
                    dynamic_growth_pct = ((current_ttm_eps - prior_ttm_eps) / prior_ttm_eps) * 100
                    calculated_pe = current_price / current_ttm_eps
                    
                    if dynamic_growth_pct > 0:
                        dynamic_peg = calculated_pe / dynamic_growth_pct
                        print(f"  -> 📈 [TTM 计算成功] 滚动同比增速: {dynamic_growth_pct:.2f}%, 动态 PEG: {dynamic_peg:.3f}")
        except Exception as q_err:
            pass # 如果财报不全则跳过，保留 None

        # 💡 【Naver 补丁】：如果是韩股且核心数据缺失，跨站打补丁
        if symbol.endswith('.KS') and (not trailing_pe or not pb_ratio):
            print(f"  -> 检测到韩股基础数据缺失，正在向 Naver 请求补丁...")
            naver_pe, naver_pb = get_naver_data(symbol)
            if naver_pe:
                trailing_pe = naver_pe
                print(f"  -> ✅ 成功从 Naver 补齐 Trailing P/E: {trailing_pe}")
            if naver_pb:
                pb_ratio = naver_pb
                print(f"  -> ✅ 成功从 Naver 补齐 P/B Ratio: {pb_ratio}")

        # 补全静态 PEG 的兜底逻辑 (如果雅虎没给 pegRatio，但给了 PE 和增速)
        if (not static_peg or pd.isna(static_peg)) and trailing_pe and static_growth:
            if static_growth > 0:
                static_peg = trailing_pe / (static_growth * 100)
            
        currency = info.get('currency', 'USD')
            
        # 构建同时包含静态和动态指标的数据字典
        data = {
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Symbol": symbol,
            f"Price ({currency})": current_price if current_price else "N/A",
            "P/B": round(pb_ratio, 2) if pb_ratio else "N/A",
            "PE(TTM)": round(trailing_pe, 2) if trailing_pe else "N/A",
            "PE(Fwd)": round(forward_pe, 2) if forward_pe else "N/A",
            "Static Growth": f"{round(static_growth * 100, 2)}%" if (static_growth and static_growth > 0) else "N/A",
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

def save_to_csv(data_list, filename="valuation_log.csv"):
    df = pd.DataFrame(data_list)
    file_exists = os.path.isfile(filename)
    df.to_csv(filename, mode='a', index=False, header=not file_exists)
    print(f"\n✅ 数据已成功存入 {filename}")

if __name__ == "__main__":
    symbols_to_track = ["000660.KS", "005930.KS", "MU", "LITE", "COHR"] 
    
    all_results = []
    all_alerts = []
    
    print("="*90)
    print(" 周期股估值多维监控系统 (双 PEG 对比版)")
    print("="*90)
    
    for sym in symbols_to_track:
        result_data, current_pb, active_peg = fetch_valuation(sym)
        if result_data:
            all_results.append(result_data)
            # 策略分析优先使用动态 PEG (如果算出)，否则退化使用静态 PEG
            alerts = analyze_signals(sym, current_pb, active_peg)
            all_alerts.extend(alerts)
            
    if all_results:
        print("\n📊 【今日数据概览】")
        # 强制格式化 DataFrame 对齐输出
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
            print("🟢 所有监控标的估值均未触及高危预警线。")
        print("="*90)
        
        save_to_csv(all_results)
